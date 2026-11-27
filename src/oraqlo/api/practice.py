"""Endpoints de la Práctica autónoma (/api/practice) y su tarea periódica.

Separado de app.py para que cada dominio de la API viva en su módulo. El estado se
comparte con la app a través de los objetos que recibe (servicio, jobs, scheduler).
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from oraqlo.services.core import OraqloService
from oraqlo.services.jobs import JobRunner
from oraqlo.services.scheduler import PeriodicTask, Scheduler


class PracticeIn(BaseModel):
    auto: bool | None = None
    interval_min: float | None = Field(None, ge=5, le=24 * 60)


def practice_router(service: OraqloService, runner: JobRunner, scheduler: Scheduler) -> APIRouter:
    """Registra la tarea periódica de práctica en `scheduler` y devuelve sus rutas."""
    router = APIRouter()

    def practice_enabled() -> bool:
        return bool(service.store.settings.get("practice_auto", service.config.practice.auto))

    def practice_interval() -> timedelta:
        minutes = service.store.settings.get(
            "practice_interval_min", service.config.practice.interval_min
        )
        return timedelta(minutes=float(minutes))

    def submit_practice() -> None:
        if runner.active("practice") == 0:
            runner.submit("practice", {})

    scheduler.add(PeriodicTask("practice", practice_interval, practice_enabled, submit_practice))

    cache: dict[str, object] = {}

    def practice_scores() -> tuple[str | None, dict]:
        """(consejo, duelos): recorren todo el ledger y la página los pide cada 30 s, así
        que se recalculan solo cuando el ledger o el modelo de práctica cambian."""
        from oraqlo.autopractice import calibration_advice, duels, practice_source

        ledger = service.store.ledger
        fallback = f"llm:{service.config.role('practica').model}"
        key = (id(ledger), ledger.version, fallback)
        if cache.get("key") != key:
            # Con comité, la Práctica registra a Oraqlo como `llm:comite`, no con el modelo del rol.
            source = practice_source(ledger, fallback)
            cache.update(key=key, value=(calibration_advice(ledger, source), duels(ledger)))
        return cache["value"]  # type: ignore[return-value]

    def practice_state() -> dict:
        from oraqlo.llm.websearch import OllamaWebClient

        advice, duel_list = practice_scores()
        last = scheduler.last_run("practice")
        return {
            "auto": practice_enabled(),
            "interval_min": practice_interval().total_seconds() / 60,
            # La misma fuente que «próxima», para que nunca se contradigan.
            "last_run": last.isoformat()
            if last
            else service.store.settings.get("practice_last_run"),
            "next_run": (last + practice_interval()).isoformat()
            if last and practice_enabled()
            else None,
            "running": runner.active("practice") > 0,
            # Sin la clave de Ollama, Práctica predice sin investigar (y acierta menos).
            "research": OllamaWebClient().is_available(),
            "advice": advice,
            "recent": runner.list(limit=20, kind="practice"),
            # Pregunta a pregunta: quién se acercó más, Oraqlo o el mercado.
            "duels": duel_list,
        }

    @router.get("/api/practice")
    def practice_get() -> dict:
        return practice_state()

    @router.put("/api/practice")
    def practice_put(body: PracticeIn) -> dict:
        if body.auto is not None:
            service.store.settings.set("practice_auto", body.auto)
        if body.interval_min is not None:
            service.store.settings.set("practice_interval_min", body.interval_min)
        return practice_state()

    @router.post("/api/practice/run", status_code=202)
    def practice_run() -> dict:
        if runner.active("practice"):
            raise HTTPException(409, "Ya hay una pasada de práctica en curso.")
        return {"job_id": runner.submit("practice", {})}

    return router
