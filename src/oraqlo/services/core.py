"""Servicio de aplicación: todos los flujos de Oraqlo, sin interfaz.

La API web (y cualquier otra interfaz) solo llama a estos métodos. Cada flujo
largo recibe `emit(tipo, datos)` para informar progreso en vivo; el JobRunner
lo persiste como eventos del trabajo y la web los recibe por SSE.

Tipos de evento: "log" {msg}, "phase" {name}, "forecast", "turn" {role, text},
"verdict", "revision", "fact" {text, url}, "questions" {items} (el flujo se pausa
para preguntar al usuario), "answers" {items} (lo que respondió, al reanudar).
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from oraqlo.agent import OraqloAgent
from oraqlo.casefile import CaseSpec, case_to_dict, parse_case
from oraqlo.config import (
    OraqloConfig,
    default_model_for,
    default_provider_and_model,
    load_config,
)
from oraqlo.drafter import draft_case
from oraqlo.forecaster.aggregate import clamp
from oraqlo.forecaster.base import Distribution, Forecast, Forecaster
from oraqlo.forecaster.committee import (
    COMMITTEE_MODEL,
    Member,
    build_members,
    member_source,
    model_weights,
    pick_committee,
)
from oraqlo.forecaster.ensemble import MEMBER_SEP, resolve_group
from oraqlo.forecaster.explain import explain
from oraqlo.forecaster.llm import LLMForecaster
from oraqlo.goals import GoalStrategist
from oraqlo.goaltrack import GoalTracker
from oraqlo.llm.ollama import (
    api_cloud_name,
    fetch_cloud_catalog,
    is_cloud_model,
    local_cloud_name,
)
from oraqlo.llm.registry import ProviderRegistry, provider_info
from oraqlo.llm.websearch import WEB_SEARCH_KEY_ENV, OllamaWebClient
from oraqlo.memory import recalibration, scorecard
from oraqlo.memory.recalibration import Recalibration, llm_source, user_resolvable
from oraqlo.panel import ROLE_PROMPTS, RoleConfig, SequentialPanel
from oraqlo.report import render_goal_report_markdown, render_report_markdown, slugify
from oraqlo.research import ResearchBrief, Researcher
from oraqlo.services import watch as watching
from oraqlo.services.serialize import forecast_json, goal_json, resolved_json, to_jsonable
from oraqlo.simulator.base import TreeSearchEngine
from oraqlo.store import Store
from oraqlo.strategy.planner import ExpectedUtilityPlanner
from oraqlo.world_model import WorldModel, WorldState

log = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], None]


class PracticeFailed(RuntimeError):
    """La pasada de práctica no logró ni predecir ni resolver nada."""


class NeedsInput(Exception):  # no es un error: es una pausa
    """El flujo se detiene para preguntarle algo al usuario.

    `result` es el resultado de este tramo (kind "questions"): las preguntas y el
    checkpoint para reanudar. El JobRunner lo guarda como resultado del job; al
    responder se lanza un job nuevo con `clarify` (ver `JobRunner.answer`)."""

    def __init__(self, result: dict) -> None:
        super().__init__("El análisis necesita datos del usuario.")
        self.result = result


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

    # ── el comité de modelos (ver forecaster.committee) ──────────────────

    CATALOG_TTL_S = 24 * 3600

    def cloud_catalog(self) -> list[dict]:
        """Modelos en la nube de ollama.com (público), guardado un día en memoria."""
        import time

        cached = getattr(self, "_catalog", None)
        if cached and time.monotonic() - cached[0] < self.CATALOG_TTL_S:
            return cached[1]
        catalog = fetch_cloud_catalog()
        if catalog:
            self._catalog = (time.monotonic(), catalog)
        return catalog

    def _cloud_route(self, provider_name: str, lead_model: str):
        """Cómo llegar a los modelos de la nube: con el Ollama del PC si el rol ya usa uno
        de la nube por ahí (sesión iniciada), o con la clave de ollama.com. None: no hay."""
        import os

        if provider_name == "ollama-local" and is_cloud_model(lead_model):
            return "ollama-local"
        if os.environ.get(WEB_SEARCH_KEY_ENV):
            return "ollama-cloud"
        return None

    def committee_models(self, provider_name: str, lead_model: str) -> list[str]:
        """Los modelos de la nube (nombres de ollama.com) que acompañan al del rol.
        `forecast.committee` sin definir = los recomendados del catálogo; [] = sin comité."""
        if self._cloud_route(provider_name, lead_model) is None:
            return []
        chosen = self.config.forecast.committee
        if chosen is None:
            chosen = pick_committee(self.cloud_catalog(), api_cloud_name(lead_model))
        lead = api_cloud_name(lead_model)
        return [m for m in dict.fromkeys(api_cloud_name(c) for c in chosen) if m and m != lead]

    def committee_view(self) -> dict:
        """Lo que la app necesita para elegir el comité del Oráculo."""
        _provider, r = self._role("oraculo")
        route = self._cloud_route(r.provider, r.model)
        catalog = self.cloud_catalog() if route else []
        lead = api_cloud_name(r.model)
        chosen = self.config.forecast.committee
        return {
            "mode": "auto" if chosen is None else ("off" if not chosen else "custom"),
            "lead": r.model,
            "available": route is not None,
            "models": self.committee_models(r.provider, r.model),
            "recommended": pick_committee(catalog, lead) if route else [],
            "catalog": [
                {"name": str(m["name"]), "size": m.get("size") or None}
                for m in catalog
                if api_cloud_name(str(m["name"])) != lead
            ],
            "samples": self.config.forecast.samples,
        }

    def _team(self, role: str, samples: int) -> tuple[Any, Any, list[Member], dict | None, str]:
        """(proveedor, rol, miembros, pesos, modelo con que se registra la previsión)."""
        provider, r = self._role(role)
        extra = []
        route = self._cloud_route(r.provider, r.model)
        names = self.committee_models(r.provider, r.model) if route else []
        if route and names:
            try:
                via = self.registry.get(route)
            except Exception:  # sin ese proveedor: sin comité
                names = []
            for model in names:
                extra.append((via, local_cloud_name(model) if route == "ollama-local" else model))
        members = build_members((provider, r.model), extra, samples)
        models = list(dict.fromkeys(m.model for m in members))
        if len(models) < 2:
            return provider, r, members, None, r.model
        return provider, r, members, model_weights(self.store.ledger, models), COMMITTEE_MODEL

    # ── opciones que la app puede cambiar sin tocar el toml ──────────────

    OPTION_KEYS: ClassVar[dict[str, tuple[str, str]]] = {
        "markets": ("forecast", "markets"),
        "decompose": ("forecast", "decompose"),
        "reference": ("forecast", "reference"),
        "watch": ("watch", "auto"),
        "watch_interval_hours": ("watch", "interval_hours"),
        "watch_daily_budget": ("watch", "daily_budget"),
        "watch_alert_points": ("watch", "alert_points"),
    }

    def options(self) -> dict:
        """Opciones de predicción y vigilancia: lo guardado en la app sobre el toml."""
        out = {}
        for name, (section, field) in self.OPTION_KEYS.items():
            default = getattr(getattr(self.config, section), field)
            out[name] = self.store.settings.get(f"options.{name}", default)
        out["metaculus"] = bool(__import__("os").environ.get("METACULUS_TOKEN"))
        out["web_search"] = OllamaWebClient().is_available()
        return out

    def set_options(self, changes: dict) -> dict:
        for name, value in changes.items():
            if name in self.OPTION_KEYS and value is not None:
                self.store.settings.set(f"options.{name}", value)
        return self.options()

    def _oracle(self, emit: Emit = _noop, extras: bool = True) -> LLMForecaster:
        """El Oráculo con su comité. `extras`: mercado, estimación por partes y clase
        de referencia (según las opciones). La Práctica no pasa por aquí: es a ciegas."""
        provider, r, members, weights, source_model = self._team(
            "oraculo", self.config.forecast.samples
        )
        advice, recal = self._learned(source_model)
        options = self.options() if extras else {}
        ledger = self.store.ledger
        finder = None
        if options.get("markets"):
            from oraqlo.forecaster.markets import MarketFinder

            rp, rr = self._role("investigador")
            finder = MarketFinder(rp, rr.model)
        reference = None
        if options.get("reference"):
            from oraqlo.memory.reference import reference_block

            reference = lambda question: reference_block(ledger, question)
        return LLMForecaster(
            provider,
            model=r.model,
            source=llm_source(source_model, "prevision"),
            advice=advice,
            recalibration=recal,
            samples=self.config.forecast.samples,
            on_progress=lambda msg: emit("log", {"msg": msg}),
            members=members,
            weights=weights,
            market_finder=finder,
            decompose=bool(options.get("decompose")),
            reference=reference,
            weigh=lambda names: model_weights(ledger, names),
        )

    def _record_voices(self, forecast: Forecast, oracle: LLMForecaster) -> None:
        """Lo que dijo cada voz del comité (modelos, mercado, estructura), aparte: al
        resolver la previsión se resuelven con ella y así sus pesos aprenden."""
        for voice, dist in oracle.last_members.items():
            self.store.ledger.record(
                Forecast(
                    id=f"{forecast.id}{MEMBER_SEP}{voice}",
                    question=forecast.question,
                    distribution=Distribution(outcomes=dict(dist)),
                    horizon=forecast.horizon,
                    assumptions=["Lo que dijo esta voz del comité, antes de combinar."],
                    source=member_source(voice),
                    created_at=forecast.created_at,
                )
            )

    # ── vigilancia (ver services.watch) ───────────────────────────────────

    @property
    def watches(self) -> watching.WatchStore:
        return watching.WatchStore(self.store.settings)

    def _start_watch(
        self,
        forecast: Forecast,
        emit: Emit = _noop,
        context: str = "",
        signals: list[watching.Signal] | None = None,
        kind: str = "forecast",
        goal_id: str | None = None,
        question: str | None = None,
    ) -> dict | None:
        """Empieza a vigilar una previsión (u objetivo). None si la vigilancia está
        apagada o no hay búsqueda web con la que vigilar."""
        options = self.options()
        if not options.get("watch") or not options.get("web_search"):
            return None
        if signals is None:
            provider, r = self._role("investigador")
            try:
                signals = watching.extract_signals(
                    provider, r.model, forecast.question, forecast.distribution.outcomes, context
                )
            except Exception as e:  # vigilar es una ayuda: nunca tumba el análisis
                emit("log", {"msg": f"No pude preparar la vigilancia ({e}).", "level": "warn"})
                signals = []
        created = forecast.created_at or datetime.now(UTC)
        w = watching.Watch(
            root=forecast.id,
            question=question or forecast.question,
            horizon_days=forecast.horizon.total_seconds() / 86400,
            created_at=created.isoformat(),
            chain=[forecast.id],
            signals=signals,
            kind=kind,
            goal_id=goal_id,
            history=[
                {
                    "at": created.isoformat(),
                    "id": forecast.id,
                    "outcomes": dict(forecast.distribution.outcomes),
                    "why": "Previsión inicial.",
                }
            ],
        )
        self.watches.save(w)
        if signals:
            emit("log", {"msg": f"Vigilaré {len(signals)} señales y te avisaré si algo cambia."})
        return w.view()

    def set_watch(self, forecast_id: str, enabled: bool) -> dict:
        w = self.watches.find(forecast_id)
        if w is None:
            ledger = self.store.ledger
            forecast = next((f for f in ledger.open_forecasts if f.id == forecast_id), None)
            if forecast is None:
                raise KeyError(f"No hay una previsión abierta con id {forecast_id}")
            view = self._start_watch(forecast)
            if view is None:
                raise ValueError("Para vigilar hace falta la búsqueda web (clave de Ollama).")
            w = self.watches.find(forecast_id)
            assert w is not None
        w.enabled = enabled
        self.watches.save(w)
        return w.view()

    def _watch_interval(self) -> timedelta:
        return timedelta(hours=float(self.options()["watch_interval_hours"]))

    def _checks_today(self) -> int:
        today = datetime.now(UTC).date().isoformat()
        state = self.store.settings.get("watch.checks", {}) or {}
        return int(state.get(today, 0))

    def _count_check(self) -> None:
        today = datetime.now(UTC).date().isoformat()
        state = self.store.settings.get("watch.checks", {}) or {}
        self.store.settings.set("watch.checks", {today: int(state.get(today, 0)) + 1})

    def watch_due(self, now: datetime | None = None) -> list[watching.Watch]:
        """Vigilancias que tocan ya, dentro del presupuesto de hoy."""
        now = now or datetime.now(UTC)
        options = self.options()
        if not options.get("web_search"):
            return []
        budget = int(options["watch_daily_budget"]) - self._checks_today()
        if budget <= 0:
            return []
        open_ids = {f.id for f in self.store.ledger.open_forecasts}
        interval = self._watch_interval()
        due = []
        for w in self.watches.all():
            if not w.due(now, interval):
                continue
            if w.kind == "goal":
                try:
                    if self.store.goals.get(w.goal_id or "").status != "activo":
                        continue
                except KeyError:
                    continue
            elif w.latest not in open_ids:
                continue
            due.append(w)
        due.sort(key=lambda w: w.last_check or "")
        return due[:budget]

    def watch_run(self, emit: Emit = _noop) -> dict:
        """Una pasada de vigilancia (la lanza el planificador como un trabajo más)."""
        checked, updated, alerts, errors = 0, [], [], []
        threshold = float(self.options()["watch_alert_points"]) / 100.0
        for w in self.watch_due():
            checked += 1
            self._count_check()
            try:
                change = self._check_watch(w, emit)
            except Exception as e:
                log.exception("Vigilancia de %s falló", w.root)
                errors.append(f"{w.question[:60]}: {e}")
                w.last_check = datetime.now(UTC).isoformat()
                self.watches.save(w)
                continue
            if change is None:
                continue
            updated.append(change)
            if change["moved"] >= threshold or change["triggered"]:
                alerts.append(change["alert"])
        return {
            "kind": "watch",
            "checked": checked,
            "updated": updated,
            "alerts": alerts,
            "errors": errors,
        }

    def _check_watch(self, w: watching.Watch, emit: Emit) -> dict | None:
        """Investiga lo nuevo; si importa, re-predice (o hace check-in del objetivo)."""
        emit("log", {"msg": f"Vigilando: {w.question[:80]}"})
        days_left = max(1, int((w.deadline - datetime.now(UTC)).total_seconds() // 86400))
        brief = self.research(watching.focus_question(w), days_left, emit)
        now = datetime.now(UTC).isoformat()
        w.last_check = now
        if brief is None:
            self.watches.save(w)
            return None
        seen = {f.casefold() for f in w.facts}
        fresh = [f.text for f in brief.facts if f.text.casefold() not in seen]
        if not fresh:
            self.watches.save(w)
            return None
        provider, r = self._role("investigador")
        current = self._current_text(w)
        verdict = watching.assess_news(provider, r.model, w, fresh, current)
        w.facts.extend(fresh)
        triggered = []
        for idx in verdict.triggered:
            if w.signals[idx].triggered_at is None:
                w.signals[idx].triggered_at = now
                triggered.append(w.signals[idx].text)
        if not verdict.relevant and not triggered:
            self.watches.save(w)
            return None
        why = verdict.summary or "Hay novedades relevantes."
        if w.kind == "goal":
            return self._recheck_goal(w, why, triggered)
        return self._reforecast(w, brief, why, triggered, emit)

    def _current_text(self, w: watching.Watch) -> str:
        last = w.history[-1]["outcomes"] if w.history else {}
        if w.kind == "goal":
            try:
                return f"P(objetivo) = {self.store.goals.get(w.goal_id or '').current_p:.0%}"
            except KeyError:
                return ""
        return ", ".join(f"{k}: {v:.0%}" for k, v in sorted(last.items(), key=lambda kv: -kv[1]))

    def _reforecast(
        self,
        w: watching.Watch,
        brief: ResearchBrief,
        why: str,
        triggered: list[str],
        emit: Emit,
    ) -> dict:
        ledger = self.store.ledger
        previous = next(f for f in ledger.open_forecasts if f.id == w.latest)
        days_left = max(1, int((w.deadline - datetime.now(UTC)).total_seconds() // 86400))
        oracle = self._oracle(emit)
        forecast = oracle.forecast(self._state_from(brief), w.question, timedelta(days=days_left))
        forecast.assumptions.append(watching.update_note(previous))
        forecast.assumptions.append(f"Qué cambió: {why}")
        provider, r = self._role("oraculo")
        with contextlib.suppress(Exception):  # la explicación es un adorno: la cifra ya está
            forecast = explain(provider, r.model, w.question, forecast)
        ledger.record(forecast)
        self._record_voices(forecast, oracle)
        old = previous.distribution.outcomes
        new = forecast.distribution.outcomes
        w.chain.append(forecast.id)
        w.history.append(
            {"at": datetime.now(UTC).isoformat(), "id": forecast.id, "outcomes": new, "why": why}
        )
        self.watches.save(w)
        delta = watching.moved(old, new)
        top = max(new, key=lambda k: new[k])
        body = f"{why} Ahora: «{top}» {new[top]:.0%}"
        if top in old:
            body += f" (antes {old[top]:.0%})."
        if triggered:
            body += " Señal: " + triggered[0]
        return {
            "root": w.root,
            "question": w.question,
            "id": forecast.id,
            "before": old,
            "after": new,
            "moved": delta,
            "why": why,
            "triggered": triggered,
            "alert": {
                "title": "Cambió una previsión",
                "body": f"{w.question[:80]} — {body}"[:300],
                "url": "/predicciones",
                "tag": f"watch-{w.root}",
            },
        }

    def _recheck_goal(self, w: watching.Watch, why: str, triggered: list[str]) -> dict | None:
        goal_id = w.goal_id or ""
        try:
            before = self.store.goals.get(goal_id).current_p
        except KeyError:
            return None
        provider, r = self._role("oraculo")
        note = f"Vigilancia automática (no es del usuario): {why}"
        if triggered:
            note += " Condiciones que cambiaron: " + "; ".join(triggered)
        checkin = GoalTracker(self.store.goals, self.store.ledger, provider, r.model).check_in(
            goal_id, note
        )
        w.history.append(
            {
                "at": datetime.now(UTC).isoformat(),
                "id": goal_id,
                "outcomes": {"se logra": checkin.p_estimate, "no se logra": 1 - checkin.p_estimate},
                "why": why,
            }
        )
        self.watches.save(w)
        delta = abs(checkin.p_estimate - before)
        body = f"{why} P(objetivo): {before:.0%} → {checkin.p_estimate:.0%}."
        if checkin.replan_recommended:
            body += " Conviene re-planificar."
        return {
            "root": w.root,
            "question": w.question,
            "id": goal_id,
            "before": {"se logra": before},
            "after": {"se logra": checkin.p_estimate},
            "moved": 1.0 if checkin.replan_recommended else delta,
            "why": why,
            "triggered": triggered,
            "alert": {
                "title": "Cambió un objetivo",
                "body": f"{w.question[:80]} — {body}"[:300],
                "url": f"/objetivos/{goal_id}",
                "tag": f"watch-{w.root}",
            },
        }

    def research(
        self,
        question: str,
        horizon_days: int,
        emit: Emit = _noop,
        resume: ResearchBrief | None = None,
    ) -> ResearchBrief | None:
        """Investigación web (opcional). Degrada con aviso; nunca bloquea el flujo.

        `resume`: brief de un tramo anterior con las respuestas del usuario."""
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
                resume=resume,
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

    def gather(
        self,
        kind: str,
        question: str,
        horizon_days: int,
        research: bool,
        emit: Emit = _noop,
        ask_user: bool = False,
        clarify: dict | None = None,
    ) -> ResearchBrief | None:
        """Investiga y, si internet no basta y `ask_user`, pausa para preguntar.

        `clarify` llega al reanudar: {"brief": checkpoint, "skip": bool}. Con
        `skip` (el usuario pulsó «Continuar sin responder») no se vuelve a preguntar.
        Lanza `NeedsInput` cuando hay preguntas; si no, devuelve el brief (con las
        respuestas del usuario dentro, si las hubo) o None.
        """
        resume = ResearchBrief.from_dict(clarify["brief"]) if clarify else None
        if resume is not None:
            emit("answers", {"items": [q.to_dict() for q in resume.clarifications]})
        brief = self.research(question, horizon_days, emit, resume=resume) if research else None
        if brief is None:
            brief = resume  # sin internet: al menos lo que contó el usuario
        if not ask_user or (clarify and clarify.get("skip")):
            return brief
        provider, r = self._role("investigador")
        try:
            questions = Researcher(OllamaWebClient(), provider, r.model).clarify(
                question, timedelta(days=horizon_days), brief
            )
        except Exception as e:  # preguntar es una ayuda: si falla, se sigue sin ella
            emit("log", {"msg": f"No pude decidir si preguntarte algo ({e}).", "level": "warn"})
            return brief
        if not questions:
            return brief
        emit("phase", {"name": "preguntar"})
        items = [q.to_dict() for q in questions]
        emit("questions", {"items": items})
        checkpoint = (brief or ResearchBrief(facts=[], queries=[])).to_dict()
        params = {"goal" if kind == "goal" else "question": question}
        raise NeedsInput(
            {
                "kind": "questions",
                "mode": kind,
                "question": question,
                "params": {**params, "horizon_days": horizon_days, "research": research},
                "questions": items,
                "checkpoint": checkpoint,
                "facts": [{"text": f.text, "url": f.source_url} for f in brief.facts]
                if brief
                else [],
            }
        )

    @staticmethod
    def _state_from(brief: ResearchBrief | None) -> WorldState:
        world = WorldModel()
        if brief is not None:
            for obs in brief.as_observations():
                world.apply(obs)
        return world.current()

    # ── flujos (los ejecuta el JobRunner) ─────────────────────────────────

    def ask_forecast(
        self,
        question: str,
        horizon_days: int = 90,
        research: bool = True,
        emit: Emit = _noop,
        ask_user: bool = False,
        clarify: dict | None = None,
    ) -> dict:
        brief = self.gather("forecast", question, horizon_days, research, emit, ask_user, clarify)
        emit("phase", {"name": "prever"})
        provider, r = self._role("oraculo")
        oracle = self._oracle(emit)
        forecast = oracle.forecast(self._state_from(brief), question, timedelta(days=horizon_days))
        emit("log", {"msg": "Redactando la respuesta en palabras llanas…"})
        forecast = explain(provider, r.model, question, forecast)
        self.store.ledger.record(forecast)
        self._record_voices(forecast, oracle)
        emit("forecast", forecast_json(forecast))
        watch = self._start_watch(forecast, emit, brief.as_context() if brief else "")
        facts = [(f.text, f.source_url) for f in brief.facts] if brief else None
        markdown = render_report_markdown(
            question,
            forecast,
            [],
            None,
            r.model,
            r.provider,
            research_facts=facts,
            clarifications=_qa(brief),
        )
        result = {
            "kind": "forecast",
            "question": question,
            "forecast": forecast_json(forecast),
            "facts": [{"text": t, "url": u} for t, u in facts or []],
            "clarifications": _qa_json(brief),
            "model": self._label("oraculo"),
            "markdown": markdown,
            "voices": _voices_json(oracle),
            "watch": watch,
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
        _provider, r = self._role("oraculo")
        world = WorldModel()
        if brief is not None:
            for obs in brief.as_observations():
                world.apply(obs)
        agent = OraqloAgent(
            connectors=[],
            world_model=world,
            forecaster=_Precomputed(forecast) if forecast is not None else self._oracle(emit),
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
        emit("phase", {"name": "sensibilidad"})
        analysis = sensitivity_analysis(spec)
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
            clarifications=_qa(brief),
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
            "clarifications": _qa_json(brief),
            "markdown": markdown + _analysis_markdown(analysis, report.verdict, spec),
            "analysis": analysis,
            "rival": _rival_json(spec),
        }
        signals = watching.signals_from_texts(
            [
                *(p["signal"] for p in report.verdict.premortem if p.get("signal")),
                *report.verdict.review_triggers,
            ]
        )
        if signals:
            result["watch"] = self._start_watch(
                report.forecast, emit, signals=signals, question=spec.question
            )
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
        group = recalibration.group_key("panel", 2, spec.horizon.days)
        if recal.active_for(group):
            outcomes = clamp(recal.apply(outcomes, group))
            assumptions.append(recal.note(group))
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
        self,
        question: str,
        horizon_days: int = 90,
        research: bool = True,
        emit: Emit = _noop,
        ask_user: bool = False,
        clarify: dict | None = None,
    ) -> dict:
        """Análisis completo: previsión calibrada → el LLM redacta el caso anclado en
        ella → ciclo con panel (que reutiliza esa misma previsión)."""
        brief = self.gather("full", question, horizon_days, research, emit, ask_user, clarify)
        emit("phase", {"name": "prever"})
        oracle_provider, oracle_role = self._role("oraculo")
        world = WorldModel()
        if brief is not None:
            for obs in brief.as_observations():
                world.apply(obs)
        oracle = self._oracle(emit)
        forecast = oracle.forecast(world.current(), question, timedelta(days=horizon_days))
        emit("log", {"msg": "Redactando la respuesta en palabras llanas…"})
        forecast = explain(oracle_provider, oracle_role.model, question, forecast)
        self._record_voices(forecast, oracle)
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
        if spec.rival is not None:
            emit("log", {"msg": f"Con rival: {spec.rival.description} (responde a cada jugada)."})
        result = self.run_case(spec, emit=emit, brief=brief, forecast=forecast)
        result["voices"] = _voices_json(oracle)
        return result

    def achieve_goal(
        self,
        goal: str,
        horizon_days: int = 90,
        research: bool = True,
        emit: Emit = _noop,
        ask_user: bool = False,
        clarify: dict | None = None,
    ) -> dict:
        brief = self.gather("goal", goal, horizon_days, research, emit, ask_user, clarify)
        provider, r, members, weights, source_model = self._team(
            "estratega", self.config.forecast.samples
        )
        advice, recal = self._learned(source_model)
        plan = GoalStrategist(
            provider,
            r.model,
            advice=advice,
            recalibration=recal,
            samples=self.config.forecast.samples,
            members=members,
            weights=weights,
            source_model=source_model,
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
        watch = self._start_watch(
            plan.with_plan,
            emit,
            signals=watching.signals_from_texts(
                [c.text for c in plan.external_conditions]
                + [p["signal"] for p in (plan.verdict.premortem if plan.verdict else [])]
            ),
            kind="goal",
            goal_id=tracked.id,
            question=goal,
        )
        facts = [(f.text, f.source_url) for f in brief.facts] if brief else None
        markdown = render_goal_report_markdown(
            plan,
            r.model,
            r.provider,
            research_facts=facts,
            tracked=tracked,
            clarifications=_qa(brief),
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
            "clarifications": _qa_json(brief),
            "markdown": markdown,
            "watch": watch,
        }
        result["run_id"] = self._save_run("goal", goal, result)
        return result

    def practice(self, research: bool = True, emit: Emit = _noop) -> dict:
        from oraqlo.autopractice import PracticeEngine
        from oraqlo.polymarket import PolymarketClient

        provider, r, members, weights, _source = self._team(
            "practica", self.config.forecast.samples
        )
        web = OllamaWebClient()
        researcher = None
        if research and web.is_available():
            rp, rr = self._role("investigador")
            # Sin las páginas de Polymarket: traerían el precio y el duelo dejaría de ser a ciegas.
            researcher = Researcher(web, rp, rr.model, exclude_domains=("polymarket.com",))
        engine = PracticeEngine(
            PolymarketClient(),
            provider,
            r.model,
            self.store.ledger,
            researcher=researcher,
            markets_per_run=self.config.practice.markets,
            members=members,
            weights=weights,
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
        old = self.watches.superseded()
        return [
            f
            for f in self.store.ledger.open_forecasts
            if user_resolvable(f.source, f.question)
            and not recalibration.is_member(f.source)
            and f.id not in old
            and f.created_at
            and f.created_at + f.horizon <= now
        ]

    def ledger_view(self) -> dict:
        ledger = self.store.ledger
        overdue = {f.id for f in self.overdue_forecasts()}
        # Las que ya deberían saberse, primero (orden estable para el resto). Lo que dijo
        # cada modelo del comité no es una predicción más: no sale en la lista.
        watches = self.watches.all()
        by_latest = {w.latest: w for w in watches if w.kind == "forecast"}
        old = {fid for w in watches for fid in w.chain[:-1]}
        open_forecasts = sorted(
            (
                f
                for f in ledger.open_forecasts
                if not recalibration.is_member(f.source) and f.id not in old
            ),
            key=lambda f: f.id not in overdue,
        )

        def with_watch(item: dict) -> dict:
            w = by_latest.get(item["id"])
            item["watch"] = w.view() if w else None
            return item

        return {
            "overdue": len(overdue),
            "open": [with_watch(forecast_json(f)) for f in open_forecasts],
            "resolved": [
                with_watch(resolved_json(r))
                for r in reversed(ledger.resolved_forecasts)
                if not recalibration.is_member(r.forecast.source) and r.forecast.id not in old
            ],
        }

    def _members_of(self, forecast_id: str, forecasts) -> list[str]:
        prefix = forecast_id + MEMBER_SEP
        return [f.id for f in forecasts if f.id.startswith(prefix)]

    def _chain(self, forecast_id: str) -> tuple[list[str], watching.Watch | None]:
        """Todas las versiones de la previsión (la vigilancia las encadena)."""
        w = self.watches.find(forecast_id)
        if w is None or w.kind != "forecast":
            return [forecast_id], None
        return list(w.chain), w

    def discard(self, forecast_id: str) -> None:
        """Descarta una predicción abierta (con sus versiones) y lo que dijo cada voz."""
        ledger = self.store.ledger
        chain, w = self._chain(forecast_id)
        open_ids = {f.id for f in ledger.open_forecasts}
        if forecast_id not in open_ids:
            raise KeyError(f"No hay una predicción abierta con id {forecast_id}")
        for fid in chain:
            if fid not in open_ids:
                continue
            members = self._members_of(fid, ledger.open_forecasts)
            ledger.discard(fid)
            for mid in members:
                ledger.discard(mid)
        if w is not None:
            self.watches.delete(w.root)

    def delete_resolved(self, forecast_id: str) -> None:
        """Borra una resuelta (con sus versiones) y lo que dijo cada voz del comité."""
        ledger = self.store.ledger
        chain, w = self._chain(forecast_id)
        resolved_ids = {r.forecast.id for r in ledger.resolved_forecasts}
        if forecast_id not in resolved_ids:
            raise KeyError(f"No hay una predicción resuelta con id {forecast_id}")
        for fid in chain:
            if fid not in resolved_ids:
                continue
            members = self._members_of(fid, (r.forecast for r in ledger.resolved_forecasts))
            ledger.delete_resolved(fid)
            for mid in members:
                ledger.delete_resolved(mid)
        if w is not None:
            self.watches.delete(w.root)

    def _outcome_for(self, forecast_id: str, outcome: str) -> str:
        """En una previsión numérica se puede resolver con el número que salió."""
        from oraqlo.forecaster import numeric

        forecast = next((f for f in self.store.ledger.open_forecasts if f.id == forecast_id), None)
        if forecast is None or outcome in forecast.distribution.outcomes:
            return outcome
        spec = numeric.bins_of(forecast)
        value = numeric.parse_number(outcome) if spec else None
        if spec is None or value is None:
            return outcome
        return numeric.label_for(spec[1], value)

    def resolve(self, forecast_id: str, outcome: str) -> list[dict]:
        from oraqlo.memory.calibration import canonical_outcome

        before = {r.forecast.id for r in self.store.ledger.resolved_forecasts}
        ledger = self.store.ledger
        by_id = {f.id: f for f in ledger.open_forecasts}
        if forecast_id not in by_id:
            raise KeyError(f"No hay una predicción abierta con id {forecast_id}")
        raw = outcome
        outcome = self._outcome_for(forecast_id, raw)
        foreseen = canonical_outcome(by_id[forecast_id].distribution.outcomes, outcome)
        foreseen_here = foreseen in by_id[forecast_id].distribution.outcomes
        chain, w = self._chain(forecast_id)
        for fid in chain:
            if fid not in by_id or fid == forecast_id:
                continue
            # Cada versión se resuelve con lo que salió traducido a SUS escenarios (sus
            # intervalos, sus etiquetas). Si la versión vigente lo contemplaba y esta no
            # (cambiaron los escenarios), no se puede puntuar con justicia: se descarta.
            own = by_id[fid].distribution.outcomes
            label = canonical_outcome(own, self._outcome_for(fid, raw))
            if label in own or not foreseen_here:
                resolve_group(ledger, fid, label)
            else:
                for mid in [fid, *self._members_of(fid, ledger.open_forecasts)]:
                    ledger.discard(mid)
        resolve_group(ledger, forecast_id, outcome)
        if w is not None:
            w.enabled = False
            self.watches.save(w)
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
        # Solo las de Oraqlo: el mercado es el rival de la Práctica (sale aparte, como
        # referencia) y las preguntas que ya se sabían no cuentan, igual que en los duelos.
        everything, market = scorecard.split_market(
            ledger.resolved_forecasts, ledger.open_forecasts
        )
        items = scorecard.select(everything, kind=kind, model=model, days=days)
        members = scorecard.committee_members(ledger.resolved_forecasts, ledger.open_forecasts)
        reference = (
            scorecard.select([*market, *members], days=days)
            if kind in (None, "practica") and not model
            else []
        )
        decided = recalibration.decided_markets(
            [*ledger.open_forecasts, *(r.forecast for r in ledger.resolved_forecasts)]
        )
        open_n = sum(
            1
            for f in ledger.open_forecasts
            if recalibration.counts_for_score(f, decided)
            and (not kind or scorecard.forecast_kind(f.source, f.question) == kind)
            and (not model or recalibration.model_of(f.source) == model)
        )
        card = scorecard.scorecard(items, bins=bins, reference=reference)
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
            # Para que la app resuelva un rol igual que config.role() con cambios aún
            # sin guardar (p. ej. qué modelo se usará si el campo queda vacío).
            "auto": dict(zip(("provider", "model"), default_provider_and_model(), strict=True)),
            "default_models": {n: default_model_for(n) for n in self.registry.names()},
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


def _voices_json(oracle: LLMForecaster) -> dict:
    """Qué dijo cada voz del comité y el detalle del mercado y de la estimación por partes."""
    return {
        "members": {k: dict(v) for k, v in oracle.last_members.items()},
        **oracle.last_extras,
    }


def sensitivity_analysis(spec: CaseSpec) -> dict | None:
    from oraqlo.strategy.sensitivity import analyze

    try:
        return analyze(spec)
    except Exception:  # un análisis extra nunca tumba la recomendación
        log.exception("El análisis de sensibilidad falló")
        return None


def _rival_json(spec: CaseSpec) -> dict | None:
    if spec.rival is None:
        return None
    return {
        "description": spec.rival.description,
        "responses": {
            a.id: [{"response": r.description, "p": p} for r, p in spec.rival.likely_responses(a)]
            for a in spec.actions
        },
    }


def _analysis_markdown(analysis: dict | None, verdict: Any, spec: CaseSpec) -> str:
    lines: list[str] = []
    if analysis and analysis.get("summary"):
        lines += ["", "## Qué haría cambiar la recomendación", ""]
        lines += [f"- {line}" for line in analysis["summary"]]
    premortem = getattr(verdict, "premortem", None) or []
    if premortem:
        lines += ["", "## Pre-mortem: si sale mal, probablemente será por…", ""]
        lines += [
            f"- {p['cause']}" + (f" — señal temprana: {p['signal']}" if p.get("signal") else "")
            for p in premortem
        ]
    rival = _rival_json(spec)
    if rival:
        lines += ["", f"## Cómo respondería {rival['description']}", ""]
        names = {a.id: a.description for a in spec.actions}
        for aid, responses in rival["responses"].items():
            top = max(responses, key=lambda x: x["p"])
            lines.append(
                f"- Si «{names[aid]}»: lo más probable es «{top['response']}» ({top['p']:.0%})."
            )
    return "\n".join(lines) + ("\n" if lines else "")


def _qa(brief: ResearchBrief | None) -> list[tuple[str, str | None]] | None:
    """Pares (pregunta, respuesta o None) que se le hicieron al usuario."""
    if brief is None or not brief.clarifications:
        return None
    return [(q.text, q.answer) for q in brief.clarifications]


def _qa_json(brief: ResearchBrief | None) -> list[dict]:
    return [{"question": q, "answer": a} for q, a in _qa(brief) or []]


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
