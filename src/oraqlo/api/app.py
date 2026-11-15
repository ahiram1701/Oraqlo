"""API web de Oraqlo (FastAPI). Sirve también el frontend compilado.

    oraqlo serve   →   http://127.0.0.1:8765   (+ `tailscale serve` para el celular)

Todo lo lento (LLM) es un job: POST devuelve {job_id} al instante y el progreso
llega por SSE en /api/jobs/{id}/events (reanudable con Last-Event-ID).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from oraqlo import __version__
from oraqlo.api.auth import AuthError, identify
from oraqlo.casefile import CaseError, case_to_dict, parse_case, template_json
from oraqlo.goaltrack import MILESTONE_STATUSES, GoalTracker
from oraqlo.services.core import OraqloService
from oraqlo.services.jobs import TERMINAL, JobRunner
from oraqlo.services.scheduler import PeriodicTask, Scheduler
from oraqlo.services.serialize import goal_json
from oraqlo.store.db import dumps, loads

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


# ── esquemas de entrada ──────────────────────────────────────────────────


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    mode: Literal["forecast", "full", "goal"] = "forecast"
    horizon_days: int = Field(90, ge=1, le=3650)
    research: bool = True


class CaseRunIn(BaseModel):
    case: dict[str, Any]
    research: bool = False


class CaseSaveIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    case: dict[str, Any]


class ResolveIn(BaseModel):
    outcome: str = Field(min_length=1)


class CheckinIn(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


class MilestoneIn(BaseModel):
    status: str


class CloseIn(BaseModel):
    achieved: bool


class PracticeIn(BaseModel):
    auto: bool | None = None
    interval_min: float | None = Field(None, ge=5, le=24 * 60)


# ── fábrica ──────────────────────────────────────────────────────────────


def create_app(service: OraqloService | None = None, background: bool = True) -> FastAPI:
    """`background=False` (tests): sin scheduler; los jobs siguen corriendo en hilos."""
    service = service or OraqloService()
    runner = JobRunner(service)
    scheduler = Scheduler(service.store.settings)
    started = time.time()

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

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        recovered = runner.recover()
        if recovered:
            log.warning("%d job(s) interrumpidos por reinicio marcados como fallidos", recovered)
        if background:
            scheduler.start()
        yield
        scheduler.stop()
        runner.shutdown(wait=False)

    app = FastAPI(title="Oraqlo", version=__version__, lifespan=lifespan)
    app.state.service = service
    app.state.runner = runner
    app.state.scheduler = scheduler

    @app.middleware("http")
    async def auth(request: Request, call_next):
        try:
            request.state.identity = identify(request, service.config.server.allowed_users)
        except AuthError as e:
            return JSONResponse({"detail": e.detail}, status_code=e.status)
        return await call_next(request)

    def job_or_404(job_id: str) -> dict:
        job = runner.get(job_id)
        if job is None:
            raise HTTPException(404, "Job no encontrado")
        return job

    # ── sistema ───────────────────────────────────────────────────────────

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "db_version": service.store.db.version,
            "uptime_s": round(time.time() - started),
            "active_jobs": runner.active(),
        }

    @app.get("/api/me")
    def me(request: Request) -> dict:
        ident = request.state.identity
        return {"login": ident.login, "name": ident.name, "via": ident.via}

    @app.get("/api/providers")
    def providers() -> dict:
        return service.providers_view()

    @app.get("/api/providers/{name}/models")
    def provider_models(name: str) -> dict:
        try:
            return {"models": service.registry.get(name).list_models()}
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except Exception as e:
            raise HTTPException(502, str(e)) from e

    # ── preguntar / casos ─────────────────────────────────────────────────

    @app.post("/api/ask", status_code=202)
    def ask(body: AskIn) -> dict:
        params = {
            "question" if body.mode != "goal" else "goal": body.question,
            "horizon_days": body.horizon_days,
            "research": body.research,
        }
        return {"job_id": runner.submit(body.mode, params)}

    @app.get("/api/cases/template")
    def case_template() -> dict:
        return json.loads(template_json())

    @app.post("/api/cases/validate")
    def case_validate(body: CaseRunIn) -> dict:
        try:
            return {"ok": True, "case": case_to_dict(parse_case(body.case))}
        except CaseError as e:
            return {"ok": False, "error": str(e)}

    @app.post("/api/cases/run", status_code=202)
    def case_run(body: CaseRunIn) -> dict:
        try:
            parse_case(body.case)
        except CaseError as e:
            raise HTTPException(422, str(e)) from e
        return {"job_id": runner.submit("case", {"case": body.case, "research": body.research})}

    @app.get("/api/cases")
    def cases_list() -> list[dict]:
        rows = service.store.db.query(
            "SELECT id, name, updated_at FROM cases ORDER BY updated_at DESC"
        )
        return [dict(r) for r in rows]

    @app.get("/api/cases/{case_id}")
    def cases_get(case_id: str) -> dict:
        rows = service.store.db.query("SELECT * FROM cases WHERE id = ?", (case_id,))
        if not rows:
            raise HTTPException(404, "Caso no encontrado")
        return {**dict(rows[0]), "data": loads(rows[0]["data"])}

    @app.put("/api/cases/{case_id}")
    def cases_put(case_id: str, body: CaseSaveIn) -> dict:
        try:
            parse_case(body.case)
        except CaseError as e:
            raise HTTPException(422, str(e)) from e
        with service.store.db.tx() as conn:
            conn.execute(
                "INSERT INTO cases (id, name, data) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE "
                "SET name = excluded.name, data = excluded.data, updated_at = datetime('now')",
                (case_id, body.name, dumps(body.case)),
            )
        return {"id": case_id}

    @app.delete("/api/cases/{case_id}", status_code=204)
    def cases_delete(case_id: str) -> None:
        with service.store.db.tx() as conn:
            conn.execute("DELETE FROM cases WHERE id = ?", (case_id,))

    # ── jobs + SSE ────────────────────────────────────────────────────────

    @app.get("/api/jobs")
    def jobs_list(limit: int = 30, kind: str | None = None) -> list[dict]:
        return runner.list(limit=limit, kind=kind)

    @app.get("/api/jobs/{job_id}")
    def jobs_get(job_id: str) -> dict:
        return job_or_404(job_id)

    @app.get("/api/jobs/{job_id}/events")
    async def jobs_events(job_id: str, request: Request, after: int = 0) -> StreamingResponse:
        job_or_404(job_id)
        last_id = request.headers.get("last-event-id")
        cursor = int(last_id) if last_id and last_id.isdigit() else after

        async def stream() -> AsyncIterator[str]:
            nonlocal cursor
            yield "retry: 3000\n\n"
            idle = 0.0
            while True:
                if await request.is_disconnected():
                    return
                events = runner.events(job_id, cursor)
                for ev in events:
                    cursor = ev["seq"]
                    payload = json.dumps(ev["data"], ensure_ascii=False)
                    yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {payload}\n\n"
                job = runner.get(job_id) or {}
                if job.get("status") in TERMINAL and not runner.events(job_id, cursor):
                    final = {
                        "status": job["status"],
                        "error": job.get("error"),
                        "run_id": job.get("run_id"),
                    }
                    yield f"event: end\ndata: {json.dumps(final, ensure_ascii=False)}\n\n"
                    return
                if events:
                    idle = 0.0
                else:
                    idle += 0.4
                    if idle >= 15:
                        idle = 0.0
                        yield ": keepalive\n\n"
                await asyncio.sleep(0.4)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── runs / informes ───────────────────────────────────────────────────

    @app.get("/api/runs")
    def runs_list(limit: int = 50) -> list[dict]:
        rows = service.store.db.query(
            "SELECT id, kind, title, created_at FROM runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    def run_or_404(run_id: str) -> dict:
        rows = service.store.db.query("SELECT * FROM runs WHERE id = ?", (run_id,))
        if not rows:
            raise HTTPException(404, "Ejecución no encontrada")
        return {**dict(rows[0]), "data": loads(rows[0]["data"])}

    @app.get("/api/runs/{run_id}")
    def runs_get(run_id: str) -> dict:
        return run_or_404(run_id)

    @app.get("/api/runs/{run_id}/report.md")
    def runs_report(run_id: str) -> PlainTextResponse:
        run = run_or_404(run_id)
        filename = service.report_filename(run)
        return PlainTextResponse(
            run["data"].get("markdown", ""),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ── ledger / calibración ──────────────────────────────────────────────

    @app.get("/api/ledger")
    def ledger() -> dict:
        return service.ledger_view()

    @app.post("/api/ledger/{forecast_id}/resolve")
    def ledger_resolve(forecast_id: str, body: ResolveIn) -> dict:
        try:
            return {"resolved": service.resolve(forecast_id, body.outcome)}
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.delete("/api/ledger/open/{forecast_id}", status_code=204)
    def ledger_discard(forecast_id: str) -> None:
        try:
            service.store.ledger.discard(forecast_id)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e

    @app.delete("/api/ledger/resolved/{forecast_id}", status_code=204)
    def ledger_delete_resolved(forecast_id: str) -> None:
        try:
            service.store.ledger.delete_resolved(forecast_id)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e

    @app.get("/api/calibration")
    def calibration() -> dict:
        return service.calibration()

    # ── objetivos ─────────────────────────────────────────────────────────

    def tracker() -> GoalTracker:
        return GoalTracker(service.store.goals, service.store.ledger)

    def goal_or_404(goal_id: str):
        try:
            return service.store.goals.get(goal_id)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e

    @app.get("/api/goals")
    def goals_list() -> list[dict]:
        return [goal_json(g) for g in reversed(service.store.goals.list_all())]

    @app.get("/api/goals/{goal_id}")
    def goals_get(goal_id: str) -> dict:
        return goal_json(goal_or_404(goal_id))

    @app.post("/api/goals/{goal_id}/checkin", status_code=202)
    def goals_checkin(goal_id: str, body: CheckinIn) -> dict:
        goal_or_404(goal_id)
        return {"job_id": runner.submit("checkin", {"goal_id": goal_id, "note": body.note})}

    @app.post("/api/goals/{goal_id}/replan", status_code=202)
    def goals_replan(goal_id: str) -> dict:
        goal_or_404(goal_id)
        return {"job_id": runner.submit("replan", {"goal_id": goal_id})}

    @app.put("/api/goals/{goal_id}/milestones/{index}")
    def goals_milestone(goal_id: str, index: int, body: MilestoneIn) -> dict:
        goal_or_404(goal_id)
        if body.status not in MILESTONE_STATUSES:
            raise HTTPException(422, f"Estado inválido; válidos: {MILESTONE_STATUSES}")
        try:
            tracker().set_milestone(goal_id, index, body.status)
        except (IndexError, ValueError) as e:
            raise HTTPException(422, str(e)) from e
        return goal_json(service.store.goals.get(goal_id))

    @app.post("/api/goals/{goal_id}/close")
    def goals_close(goal_id: str, body: CloseIn) -> dict:
        goal_or_404(goal_id)
        try:
            resolved = tracker().close(goal_id, body.achieved)
        except Exception as e:
            raise HTTPException(422, str(e)) from e
        return {"goal": goal_json(service.store.goals.get(goal_id)), "resolved": resolved}

    @app.delete("/api/goals/{goal_id}", status_code=204)
    def goals_delete(goal_id: str) -> None:
        goal_or_404(goal_id)
        tracker().delete(goal_id)

    # ── práctica ──────────────────────────────────────────────────────────

    def practice_state() -> dict:
        from oraqlo.autopractice import MARKET_SOURCE, calibration_advice

        ledger = service.store.ledger
        source = f"llm:{service.config.role('practica').model}"
        last = scheduler.last_run("practice")
        return {
            "auto": practice_enabled(),
            "interval_min": practice_interval().total_seconds() / 60,
            "last_run": service.store.settings.get("practice_last_run"),
            "next_run": (last + practice_interval()).isoformat()
            if last and practice_enabled()
            else None,
            "running": runner.active("practice") > 0,
            "reliability_oraqlo": ledger.reliability(source),
            "reliability_market": ledger.reliability(MARKET_SOURCE),
            "advice": calibration_advice(ledger, source),
            "recent": runner.list(limit=5, kind="practice"),
        }

    @app.get("/api/practice")
    def practice_get() -> dict:
        return practice_state()

    @app.put("/api/practice")
    def practice_put(body: PracticeIn) -> dict:
        if body.auto is not None:
            service.store.settings.set("practice_auto", body.auto)
        if body.interval_min is not None:
            service.store.settings.set("practice_interval_min", body.interval_min)
        return practice_state()

    @app.post("/api/practice/run", status_code=202)
    def practice_run() -> dict:
        if runner.active("practice"):
            raise HTTPException(409, "Ya hay una pasada de práctica en curso.")
        return {"job_id": runner.submit("practice", {})}

    # ── frontend estático (SPA) ───────────────────────────────────────────

    index = STATIC_DIR / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Ruta de API desconocida")
        target = (STATIC_DIR / path).resolve()
        if path and target.is_file() and STATIC_DIR.resolve() in target.parents:
            return FileResponse(target)
        if index.exists():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return PlainTextResponse(
            "Oraqlo API en marcha. El frontend no está compilado (cd web && npm run build).",
            status_code=200,
        )

    return app
