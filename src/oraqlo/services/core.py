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
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from oraqlo.agent import OraqloAgent
from oraqlo.casefile import CaseSpec, case_to_dict, parse_case
from oraqlo.config import OraqloConfig, load_config
from oraqlo.drafter import draft_case
from oraqlo.forecaster.aggregate import clamp
from oraqlo.forecaster.base import Distribution, Forecast, Forecaster
from oraqlo.forecaster.ensemble import resolve_group
from oraqlo.forecaster.explain import explain
from oraqlo.forecaster.llm import LLMForecaster
from oraqlo.goals import GoalStrategist
from oraqlo.goaltrack import GoalTracker
from oraqlo.llm.ollama import is_cloud_model
from oraqlo.llm.registry import ProviderRegistry, provider_info
from oraqlo.llm.websearch import WEB_SEARCH_KEY_ENV, OllamaWebClient
from oraqlo.memory import recalibration, scorecard
from oraqlo.memory.recalibration import Recalibration, llm_source, user_resolvable
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
                {
                    "msg": "Sin clave de búsqueda web: continúo sin investigar en internet.",
                    "level": "warn",
                    "code": "no_websearch_key",
                },
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
        emit("log", {"msg": "Redactando la respuesta en palabras llanas…"})
        forecast = explain(provider, r.model, question, forecast)
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
        forecast: Forecast | None = None,
    ) -> dict:
        """`forecast`: previsión ya calculada para esta pregunta (el análisis completo
        la hace antes de redactar el caso); si no, la calcula el oráculo aquí."""
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
            forecaster=_Precomputed(forecast)
            if forecast is not None
            else self._oracle(r.model, provider, self.config.forecast.samples, emit),
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
        if report.verdict.p_success is not None and report.verdict.decision == "emitir":
            p = self._record_panel(spec, report, r.model)
            report.verdict = replace(report.verdict, p_success=p)
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

    def _record_panel(self, spec: CaseSpec, report: Any, model: str) -> float:
        """La probabilidad de éxito del Oráculo, recalibrada y anotada en el ledger.

        Así se puede resolver más adelante y la cifra del análisis completo aprende
        del historial como las demás. Devuelve la probabilidad ya recalibrada."""
        p = float(report.verdict.p_success)
        outcomes = {"sí": p, "no": 1.0 - p}
        assumptions = [
            f"Probabilidad de éxito que dio el Oráculo del panel para «{spec.question}»."
        ]
        _advice, recal = self._learned(model)
        if recal.active:
            outcomes = clamp(recal.apply(outcomes))
            assumptions.append(recal.note())
        distribution = Distribution(outcomes=outcomes)
        distribution.validate()
        self.store.ledger.record(
            Forecast(
                id=str(uuid.uuid4()),
                question=f"¿Saldrá bien «{report.recommendation.action.description}»?",
                distribution=distribution,
                horizon=spec.horizon,
                assumptions=assumptions,
                source=llm_source(model, "panel"),
                created_at=datetime.now(UTC),
            )
        )
        return outcomes["sí"]

    def ask_full(
        self, question: str, horizon_days: int = 90, research: bool = True, emit: Emit = _noop
    ) -> dict:
        """Análisis completo: previsión calibrada → el LLM redacta el caso anclado en
        ella → ciclo con panel (que reutiliza esa misma previsión)."""
        brief = self.research(question, horizon_days, emit) if research else None
        emit("phase", {"name": "prever"})
        oracle_provider, oracle_role = self._role("oraculo")
        world = WorldModel()
        if brief is not None:
            for obs in brief.as_observations():
                world.apply(obs)
        forecast = self._oracle(
            oracle_role.model, oracle_provider, self.config.forecast.samples, emit
        ).forecast(world.current(), question, timedelta(days=horizon_days))
        emit("log", {"msg": "Redactando la respuesta en palabras llanas…"})
        forecast = explain(oracle_provider, oracle_role.model, question, forecast)
        emit("phase", {"name": "redactar"})
        provider, r = self._role("redactor")
        context = "\n\n".join(
            part for part in (brief.as_context() if brief else None, anchor_text(forecast)) if part
        )
        spec = draft_case(
            provider,
            r.model,
            question,
            horizon_days,
            temperature=r.temperature,
            context=context,
        )
        emit("log", {"msg": f"Caso redactado: {len(spec.actions)} acciones candidatas."})
        return self.run_case(spec, emit=emit, brief=brief, forecast=forecast)

    def achieve_goal(
        self, goal: str, horizon_days: int = 90, research: bool = True, emit: Emit = _noop
    ) -> dict:
        brief = self.research(goal, horizon_days, emit) if research else None
        provider, r = self._role("estratega")
        advice, recal = self._learned(r.model)
        plan = GoalStrategist(
            provider,
            r.model,
            advice=advice,
            recalibration=recal,
            samples=self.config.forecast.samples,
        ).pursue(
            goal,
            timedelta(days=horizon_days),
            self._panel(),
            self.store.ledger,
            state=self._state_from(brief) if brief else None,
            brief=brief,
            on_progress=lambda msg: emit("log", {"msg": msg}),
            on_phase=lambda name: emit("phase", {"name": name}),
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
            "horizon_days": horizon_days,  # para «Repetir pregunta» con el mismo plazo
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

    def overdue_forecasts(self, now: datetime | None = None) -> list[Forecast]:
        """Abiertas que ya deberían saberse y que tiene que resolver el usuario."""
        now = now or datetime.now(UTC)
        return [
            f
            for f in self.store.ledger.open_forecasts
            if user_resolvable(f.source, f.question)
            and f.created_at
            and f.created_at + f.horizon <= now
        ]

    def ledger_view(self) -> dict:
        ledger = self.store.ledger
        overdue = {f.id for f in self.overdue_forecasts()}
        # Las que ya deberían saberse, primero (orden estable para el resto).
        open_forecasts = sorted(ledger.open_forecasts, key=lambda f: f.id not in overdue)
        return {
            "overdue": len(overdue),
            "open": [forecast_json(f) for f in open_forecasts],
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

    def calibration(
        self,
        kind: str | None = None,
        model: str | None = None,
        days: int | None = None,
        bins: int = 10,
    ) -> dict:
        """Cómo de bien acierta: resumen frente al azar, fiabilidad, tendencia,
        desgloses y peores fallos (ver memory.scorecard), con filtros."""
        ledger = self.store.ledger
        everything = ledger.resolved_forecasts
        items = scorecard.select(everything, kind=kind, model=model, days=days)
        open_n = sum(
            1
            for f in ledger.open_forecasts
            if (not kind or scorecard.forecast_kind(f.source, f.question) == kind)
            and (not model or recalibration.model_of(f.source) == model)
        )
        card = scorecard.scorecard(items, bins=bins)
        card["summary"]["open"] = open_n
        return {
            "n": len(items),
            "filters": {
                "kinds": sorted(
                    {
                        scorecard.forecast_kind(r.forecast.source, r.forecast.question)
                        for r in everything
                    },
                    key=scorecard.KINDS.index,
                ),
                "models": sorted(
                    {m for r in everything if (m := recalibration.model_of(r.forecast.source))}
                ),
            },
            **card,
            # Corrección aprendida por modelo de IA (se aplica a las previsiones nuevas).
            "recalibration": [
                {"model": m, **rc.as_dict()} for m, rc in recalibration.by_model(ledger).items()
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

        web_key = os.environ.get(WEB_SEARCH_KEY_ENV)
        return {
            "providers": [entry(n) for n in self.registry.names()],
            "roles": [self.config.role(r).model_dump() for r in ROLES],
            "configured": toml_roles(self.config.home),
            "web_search": {
                "api_key_env": WEB_SEARCH_KEY_ENV,
                "key_present": bool(web_key),
                "key_hint": key_hint(web_key),
            },
        }

    def key_envs(self) -> set[str]:
        """Variables de clave que la app puede escribir: las de proveedores conocidos y
        la de la búsqueda web (que se usa con cualquier proveedor, también Ollama local)."""
        return {
            env
            for n in self.registry.names()
            if (env := provider_info(n, self.config).api_key_env) is not None
        } | {WEB_SEARCH_KEY_ENV}

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
            # Un modelo en la nube («…:cloud») no se descarga: basta con que Ollama responda.
            if not {m for m in wanted if not is_cloud_model(m)} <= have:
                return True
        return False

    def ollama_status(self) -> dict:
        """Lo que hace falta saber de Ollama en una pantalla: el del PC, la clave de
        ollama.com y la búsqueda web (que usa esa misma clave)."""
        import os

        from oraqlo.services.userconfig import key_hint

        try:
            self.registry.get("ollama-local").list_models()
            local = True
        except Exception:
            local = False
        key = os.environ.get(WEB_SEARCH_KEY_ENV)
        return {
            "local": local,
            "key_present": bool(key),
            "key_hint": key_hint(key) if key else None,
            "key_env": WEB_SEARCH_KEY_ENV,
            "web_search": bool(key),
        }

    def report_filename(self, run: dict) -> str:
        return f"{run['created_at'][:10]}-{slugify(run['title'])}.md"


def _dumps(value: Any) -> str:
    from oraqlo.store.db import dumps

    return dumps(value)


class _Precomputed(Forecaster):
    """Devuelve una previsión ya hecha (el ciclo la registra igual en el ledger)."""

    def __init__(self, forecast: Forecast) -> None:
        self._forecast = forecast

    def forecast(self, state: WorldState, question: str, horizon: timedelta) -> Forecast:
        return self._forecast


def anchor_text(forecast: Forecast) -> str:
    """La previsión calibrada como ancla del redactor (que si no inventa las cifras)."""
    lines = [
        "Previsión calibrada de Oraqlo para esta pregunta (escenario sin intervenir). "
        "Úsala como referencia: los resultados de esperar o no hacer nada deberían "
        "parecerse a ella, y cada acción solo debería moverlos lo que justifique:"
    ]
    for label, prob in sorted(forecast.distribution.outcomes.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {label}: {prob:.0%}")
    return "\n".join(lines)
