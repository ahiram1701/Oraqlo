"""Servicio de aplicación: todos los flujos de Oraqlo, sin interfaz.

La API web (y cualquier otra interfaz) solo llama a estos métodos. Cada flujo
largo recibe `emit(tipo, datos)` para informar progreso en vivo; el JobRunner
lo persiste como eventos del trabajo y la web los recibe por SSE.

Tipos de evento: "log" {msg}, "phase" {name}, "forecast", "turn" {role, text},
"verdict", "revision", "fact" {text, url}.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from oraqlo.agent import OraqloAgent
from oraqlo.casefile import CaseSpec, case_to_dict, parse_case
from oraqlo.config import OraqloConfig, load_config
from oraqlo.drafter import draft_case
from oraqlo.forecaster.ensemble import resolve_group
from oraqlo.forecaster.llm import LLMForecaster
from oraqlo.goals import GoalStrategist
from oraqlo.goaltrack import GoalTracker
from oraqlo.llm.registry import ProviderRegistry, provider_info
from oraqlo.llm.websearch import OllamaWebClient
from oraqlo.memory import recalibration
from oraqlo.memory.calibration import ResolvedForecast
from oraqlo.memory.recalibration import Recalibration, llm_source
from oraqlo.panel import ROLE_PROMPTS, RoleConfig, SequentialPanel
from oraqlo.report import render_goal_report_markdown, render_report_markdown, slugify
from oraqlo.research import ResearchBrief, Researcher
from oraqlo.services.serialize import forecast_json, goal_json, resolved_json, to_jsonable
from oraqlo.simulator.base import TreeSearchEngine
from oraqlo.store import Store
from oraqlo.strategy.planner import ExpectedUtilityPlanner
from oraqlo.world_model import WorldModel, WorldState

log = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], None]


class PracticeFailed(RuntimeError):
    """La pasada de práctica no logró ni predecir ni resolver nada."""


def _noop(kind: str, data: dict[str, Any]) -> None:
    pass


class OraqloService:
    def __init__(self, config: OraqloConfig | None = None, store: Store | None = None) -> None:
        self.config = config or load_config()
        self.store = store or Store(self.config.db_path)
        self.registry = ProviderRegistry(self.config)

    # ── utilidades ────────────────────────────────────────────────────────

    def _role(self, name: str):
        provider, resolved = self.registry.for_role(name)
        return provider, resolved

    def _panel(self) -> SequentialPanel:
        roles = {}
        for name in ROLE_PROMPTS:
            provider, r = self._role(name)
            roles[name] = RoleConfig(
                name=name, provider=provider, model=r.model, temperature=r.temperature
            )
        return SequentialPanel(roles=roles)

    def _label(self, role: str) -> str:
        r = self.config.role(role)
        return f"{r.provider}:{r.model}"

    def _save_run(self, kind: str, title: str, data: dict) -> str:
        run_id = str(uuid.uuid4())
        with self.store.db.tx() as conn:
            conn.execute(
                "INSERT INTO runs (id, kind, title, data) VALUES (?, ?, ?, ?)",
                (run_id, kind, title, _dumps(data)),
            )
        return run_id

    def _learned(self, model: str) -> tuple[str | None, Recalibration]:
        """Lo que el historial resuelto de `model` enseña: un consejo para el prompt
        (de la práctica, con resultados objetivos) y la corrección numérica."""
        from oraqlo.autopractice import calibration_advice

        ledger = self.store.ledger
        return calibration_advice(ledger, llm_source(model)), recalibration.for_model(ledger, model)

    def _oracle(self, model: str, provider, samples: int = 1, emit: Emit = _noop):
        advice, recal = self._learned(model)
        return LLMForecaster(
            provider,
            model=model,
            source=llm_source(model, "prevision"),
            advice=advice,
            recalibration=recal,
            samples=samples,
            on_progress=lambda msg: emit("log", {"msg": msg}),
        )

    def research(
        self, question: str, horizon_days: int, emit: Emit = _noop
    ) -> ResearchBrief | None:
        """Investigación web (opcional). Degrada con aviso; nunca bloquea el flujo."""
        web = OllamaWebClient()
        if not web.is_available():
            emit(
                "log",
                {"msg": "Sin OLLAMA_API_KEY: continúo sin investigación web.", "level": "warn"},
            )
            return None
        emit("phase", {"name": "investigar"})
        provider, r = self._role("investigador")
        try:
            brief = Researcher(web, provider, r.model).investigate(
                question,
                timedelta(days=horizon_days),
                on_progress=lambda msg: emit("log", {"msg": msg}),
            )
        except Exception as e:  # la investigación es opcional por diseño
            emit(
                "log",
                {"msg": f"Investigación no disponible ({e}); continúo sin ella.", "level": "warn"},
            )
            return None
        for fact in brief.facts:
            emit("fact", {"text": fact.text, "url": fact.source_url})
        return brief

    @staticmethod
    def _state_from(brief: ResearchBrief | None) -> WorldState:
        world = WorldModel()
        if brief is not None:
            for obs in brief.as_observations():
                world.apply(obs)
        return world.current()

    # ── flujos (los ejecuta el JobRunner) ─────────────────────────────────

    def ask_forecast(
        self, question: str, horizon_days: int = 90, research: bool = True, emit: Emit = _noop
    ) -> dict:
        brief = self.research(question, horizon_days, emit) if research else None
        emit("phase", {"name": "prever"})
        provider, r = self._role("oraculo")
        oracle = self._oracle(r.model, provider, self.config.forecast.samples, emit)
        forecast = oracle.forecast(self._state_from(brief), question, timedelta(days=horizon_days))
        self.store.ledger.record(forecast)
        emit("forecast", forecast_json(forecast))
        facts = [(f.text, f.source_url) for f in brief.facts] if brief else None
        markdown = render_report_markdown(
            question, forecast, [], None, r.model, r.provider, research_facts=facts
        )
        result = {
            "kind": "forecast",
            "question": question,
            "forecast": forecast_json(forecast),
            "facts": [{"text": t, "url": u} for t, u in facts or []],
            "model": self._label("oraculo"),
            "markdown": markdown,
        }
        result["run_id"] = self._save_run("forecast", question, result)
        return result

    def run_case(
        self,
        case: dict | CaseSpec,
        research: bool = False,
        emit: Emit = _noop,
        brief: ResearchBrief | None = None,
    ) -> dict:
        spec = case if isinstance(case, CaseSpec) else parse_case(case)
        if research and brief is None:
            brief = self.research(spec.question, spec.horizon.days, emit)
        provider, r = self._role("oraculo")
        world = WorldModel()
        if brief is not None:
            for obs in brief.as_observations():
                world.apply(obs)
        agent = OraqloAgent(
            connectors=[],
            world_model=world,
            forecaster=self._oracle(r.model, provider),
            simulator=TreeSearchEngine(spec.outcome_model(), spec.utility_fn()),
            planner=ExpectedUtilityPlanner(),
            panel=self._panel(),
            ledger=self.store.ledger,
            risk=spec.risk,
        )
        report = agent.cycle(
            spec.question,
            spec.actions,
            spec.horizon,
            depth=spec.depth,
            on_event=emit,
            extra_context=brief.as_context() if brief else "",
        )
        facts = [(f.text, f.source_url) for f in brief.facts] if brief else None
        ranking = [report.recommendation]
        markdown = render_report_markdown(
            spec.question,
            report.forecast,
            ranking,
            report.verdict,
            self._label("juez"),
            "panel",
            research_facts=facts,
        )
        result = {
            "kind": "cycle",
            "question": spec.question,
            "case": case_to_dict(spec),
            "forecast": forecast_json(report.forecast),
            "recommendation": to_jsonable(report.recommendation),
            "verdict": to_jsonable(report.verdict),
            "revisions": report.revisions,
            "candidates_tried": report.candidates_tried,
            "facts": [{"text": t, "url": u} for t, u in facts or []],
            "markdown": markdown,
        }
        result["run_id"] = self._save_run("cycle", spec.question, result)
        return result

    def ask_full(
        self, question: str, horizon_days: int = 90, research: bool = True, emit: Emit = _noop
    ) -> dict:
        """Análisis completo: el LLM redacta el caso y se ejecuta el ciclo con panel."""
        brief = self.research(question, horizon_days, emit) if research else None
        emit("phase", {"name": "redactar"})
        provider, r = self._role("redactor")
        spec = draft_case(
            provider,
            r.model,
            question,
            horizon_days,
            temperature=r.temperature,
            context=brief.as_context() if brief else None,
        )
        emit("log", {"msg": f"Caso redactado: {len(spec.actions)} acciones candidatas."})
        return self.run_case(spec, emit=emit, brief=brief)

    def achieve_goal(
        self, goal: str, horizon_days: int = 90, research: bool = True, emit: Emit = _noop
    ) -> dict:
        brief = self.research(goal, horizon_days, emit) if research else None
        provider, r = self._role("estratega")
        advice, recal = self._learned(r.model)
        plan = GoalStrategist(provider, r.model, advice=advice, recalibration=recal).pursue(
            goal,
            timedelta(days=horizon_days),
            self._panel(),
            self.store.ledger,
            state=self._state_from(brief) if brief else None,
            brief=brief,
            on_progress=lambda msg: emit("log", {"msg": msg}),
        )
        tracked = GoalTracker(self.store.goals, self.store.ledger).track(plan)
        facts = [(f.text, f.source_url) for f in brief.facts] if brief else None
        markdown = render_goal_report_markdown(
            plan, r.model, r.provider, research_facts=facts, tracked=tracked
        )
        result = {
            "kind": "goal",
            "question": goal,
            "goal_id": tracked.id,
            "baseline_p": plan.baseline_p,
            "with_plan_p": plan.with_plan_p,
            "uplift": plan.uplift,
            "plan": {
                "summary": plan.summary,
                "levers": to_jsonable(plan.levers),
                "external_conditions": to_jsonable(plan.external_conditions),
                "steps": to_jsonable(plan.steps),
                "milestones": to_jsonable(plan.milestones),
                "revised": plan.revised,
            },
            "verdict": to_jsonable(plan.verdict),
            "facts": [{"text": t, "url": u} for t, u in facts or []],
            "markdown": markdown,
        }
        result["run_id"] = self._save_run("goal", goal, result)
        return result

    def practice(self, research: bool = True, emit: Emit = _noop) -> dict:
        from oraqlo.autopractice import PracticeEngine
        from oraqlo.polymarket import PolymarketClient

        provider, r = self._role("practica")
        web = OllamaWebClient()
        researcher = None
        if research and web.is_available():
            rp, rr = self._role("investigador")
            researcher = Researcher(web, rp, rr.model)
        engine = PracticeEngine(
            PolymarketClient(),
            provider,
            r.model,
            self.store.ledger,
            researcher=researcher,
            markets_per_run=self.config.practice.markets,
        )
        report = engine.run_once(
            research=researcher is not None, on_progress=lambda msg: emit("log", {"msg": msg})
        )
        if report.errors and not report.predicted and not report.resolved:
            # Nada salió bien: es un fallo (rojo y con aviso), no un "listo" engañoso.
            raise PracticeFailed("; ".join(dict.fromkeys(report.errors))[:400])
        for error in report.errors:
            emit("log", {"msg": f"Aviso: {error}"})
        result = {
            "kind": "practice",
            "predicted": [
                {"market": to_jsonable(m), "forecast": forecast_json(f)}
                for m, f in report.predicted
            ],
            "resolved": [resolved_json(x) for x in report.resolved],
            "advice": report.advice,
            "reliability_oraqlo": report.reliability_oraqlo,
            "reliability_market": report.reliability_market,
            "errors": report.errors,
        }
        self.store.settings.set("practice_last_run", datetime.now(UTC).isoformat())
        return result

    def goal_checkin(self, goal_id: str, note: str, emit: Emit = _noop) -> dict:
        provider, r = self._role("oraculo")
        checkin = GoalTracker(self.store.goals, self.store.ledger, provider, r.model).check_in(
            goal_id, note
        )
        return {"checkin": to_jsonable(checkin), "goal": goal_json(self.store.goals.get(goal_id))}

    def goal_replan(self, goal_id: str, emit: Emit = _noop) -> dict:
        provider, r = self._role("estratega")
        goal = GoalTracker(self.store.goals, self.store.ledger, provider, r.model).replan(goal_id)
        return {"goal": goal_json(goal)}

    # ── operaciones rápidas (síncronas) ──────────────────────────────────

    def ledger_view(self) -> dict:
        ledger = self.store.ledger
        return {
            "open": [forecast_json(f) for f in ledger.open_forecasts],
            "resolved": [resolved_json(r) for r in reversed(ledger.resolved_forecasts)],
        }

    def resolve(self, forecast_id: str, outcome: str) -> list[dict]:
        before = {r.forecast.id for r in self.store.ledger.resolved_forecasts}
        resolve_group(self.store.ledger, forecast_id, outcome)
        return [
            resolved_json(r)
            for r in self.store.ledger.resolved_forecasts
            if r.forecast.id not in before
        ]

    def calibration(self, bins: int = 10) -> dict:
        """Brier por fuente + diagrama de fiabilidad (prob. predicha vs frecuencia real)."""
        resolved: list[ResolvedForecast] = self.store.ledger.resolved_forecasts
        by_source: dict[str, list[float]] = {}
        buckets = [[0, 0] for _ in range(bins)]  # [n, aciertos]
        for r in resolved:
            by_source.setdefault(r.forecast.source, []).append(r.brier)
            for label, p in r.forecast.distribution.outcomes.items():
                i = min(int(p * bins), bins - 1)
                buckets[i][0] += 1
                buckets[i][1] += int(label == r.actual_outcome)
        return {
            "n": len(resolved),
            "sources": sorted(
                (
                    {"source": s, "n": len(v), "brier": sum(v) / len(v)}
                    for s, v in by_source.items()
                ),
                key=lambda x: x["brier"],
            ),
            "reliability": [
                {
                    "bin": i,
                    "p_mid": (i + 0.5) / bins,
                    "n": n,
                    "observed": (hits / n) if n else None,
                }
                for i, (n, hits) in enumerate(buckets)
            ],
            # Corrección aprendida por modelo de IA (se aplica a las previsiones nuevas).
            "recalibration": [
                {"model": m, **rc.as_dict()}
                for m, rc in recalibration.by_model(self.store.ledger).items()
            ],
        }

    def providers_view(self) -> dict:
        import os

        from oraqlo.config import ROLES
        from oraqlo.services.userconfig import key_hint, toml_roles

        def entry(n: str) -> dict:
            info = provider_info(n, self.config)
            return {
                "name": n,
                "kind": info.kind,
                "api_key_env": info.api_key_env,
                "key_present": info.key_present,
                "key_hint": key_hint(os.environ.get(info.api_key_env))
                if info.api_key_env
                else None,
            }

        return {
            "providers": [entry(n) for n in self.registry.names()],
            "roles": [self.config.role(r).model_dump() for r in ROLES],
            "configured": toml_roles(self.config.home),
        }

    def key_envs(self) -> set[str]:
        """Variables de clave que la app puede escribir (las de proveedores conocidos)."""
        return {
            env
            for n in self.registry.names()
            if (env := provider_info(n, self.config).api_key_env) is not None
        }

    def reload_config(self, config: OraqloConfig | None = None) -> None:
        """Aplica una config nueva sin reiniciar. Se actualiza el MISMO objeto (otras
        piezas guardan referencias a él) y se vacía la caché de proveedores: los
        análisis en curso terminan con el proveedor que ya tenían."""
        new = config or load_config(home=self.config.home)
        for field in type(self.config).model_fields:
            setattr(self.config, field, getattr(new, field))
        self.registry.config = self.config
        self.registry.clear()

    def needs_setup(self) -> bool:
        """¿Falta algo para poder preguntar? Una clave de un proveedor en uso, o un
        Ollama local que no responde o no tiene descargado el modelo elegido."""
        from oraqlo.config import ROLES

        roles = [self.config.role(r) for r in ROLES]
        for name in {r.provider for r in roles}:
            if provider_info(name, self.config).key_present is False:
                return True
        wanted = {r.model for r in roles if r.provider == "ollama-local"}
        if wanted:
            try:
                models = self.registry.get("ollama-local").list_models()
            except Exception:
                return True
            have = set(models) | {m.removesuffix(":latest") for m in models}
            if not wanted <= have:
                return True
        return False

    def report_filename(self, run: dict) -> str:
        return f"{run['created_at'][:10]}-{slugify(run['title'])}.md"


def _dumps(value: Any) -> str:
    from oraqlo.store.db import dumps

    return dumps(value)
