"""API web de Oraqlo (FastAPI). Sirve también el frontend compilado.

    oraqlo serve   →   http://127.0.0.1:8765   (+ `tailscale serve` para el celular)

Todo lo lento (LLM) es un job: POST devuelve {job_id} al instante y el progreso
llega por SSE en /api/jobs/{id}/events (reanudable con Last-Event-ID).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from pydantic import BaseModel, Field

from oraqlo import __version__, windows_handover
from oraqlo.api.auth import AuthError, check_request, identify
from oraqlo.casefile import CaseError, case_to_dict, parse_case, template_json
from oraqlo.goaltrack import MILESTONE_STATUSES, GoalTracker, GoalTrackError
from oraqlo.services.core import OraqloService
from oraqlo.services.doctor import probe_model, probe_provider, probe_web_search, run_checks
from oraqlo.services.jobs import TERMINAL, JobRunner
from oraqlo.services.network import (
    HTTPS_PORT,
    MAX_PORT,
    MIN_PORT,
    TailscaleServe,
    port_in_use,
    published_https,
    set_published_https,
    write_port_file,
)
from oraqlo.services.notify import SETTING as NOTIFY_SETTING
from oraqlo.services.notify import DesktopNotifier
from oraqlo.services.notify import supported as notify_supported
from oraqlo.services.push import PushService, job_notification, practice_alert
from oraqlo.services.scheduler import PeriodicTask, Scheduler
from oraqlo.services.serialize import goal_json, ledger_to_csv
from oraqlo.services.userconfig import ConfigError, apply_changes, set_secret, toml_roles
from oraqlo.store.db import dumps, loads
from oraqlo.updater import UpdateError, Updater, is_managed, is_newer, refresh_launcher

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


# ── esquemas de entrada ──────────────────────────────────────────────────


class ModelTestIn(BaseModel):
    model: str | None = Field(None, max_length=200)


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


class GoalEditIn(BaseModel):
    goal: str | None = Field(None, min_length=1, max_length=500)
    deadline: str | None = None  # AAAA-MM-DD


class MilestoneEditIn(BaseModel):
    text: str | None = Field(None, min_length=1, max_length=300)
    target_date: str | None = None  # AAAA-MM-DD
    status: str | None = None


class CloseIn(BaseModel):
    achieved: bool


class PushSubIn(BaseModel):
    endpoint: str
    keys: dict[str, str]
    expirationTime: float | None = None


class EndpointIn(BaseModel):
    endpoint: str


class PracticeIn(BaseModel):
    auto: bool | None = None
    interval_min: float | None = Field(None, ge=5, le=24 * 60)


# ── fábrica ──────────────────────────────────────────────────────────────


JOB_RETENTION_DAYS = 60


def prune_backups(backups_dir: Path, daily: int = 7, pre_update: int = 3) -> None:
    """Conserva los últimos `daily` backups diarios y `pre_update` previos a updates."""
    for pattern, keep in (("daily-*.db", daily), ("pre-*.db", pre_update)):
        files = sorted(backups_dir.glob(pattern), key=lambda f: f.stat().st_mtime)
        for old in files[: max(0, len(files) - keep)]:
            old.unlink(missing_ok=True)


class PortChange(BaseModel):
    port: int = Field(ge=MIN_PORT, le=MAX_PORT)


class KeyIn(BaseModel):
    value: str = Field(min_length=1, max_length=1000)


class ConfigIn(BaseModel):
    # {rol: {provider, model, temperature, effort}}; None o "" quita el ajuste.
    roles: dict[str, dict[str, Any]] | None = None
    updates_mode: Literal["auto", "notify", "off"] | None = None
    allowed_users: list[str] | None = None


class LegacyIn(BaseModel):
    # nombre de archivo (oraqlo_ledger.json, ...) -> contenido JSON como texto
    files: dict[str, str]


class TailscaleIn(BaseModel):
    enabled: bool


class ToggleIn(BaseModel):
    enabled: bool


LEGACY_FILES = ("oraqlo_ledger.json", "oraqlo_goals.json", "oraqlo_settings.json")
LOCAL_ONLY = (
    "Esto solo se puede cambiar desde el PC donde corre Oraqlo. Ábrelo allí desde el menú Inicio."
)


def _message(e: Exception) -> str:
    """Texto de un error para el usuario: str(KeyError) añade comillas de más."""
    return str(e.args[0]) if e.args else str(e)


def require_local(request: Request) -> None:
    """Claves y ajustes del sistema: solo desde el propio PC, nunca por la tailnet."""
    if request.state.identity.via != "local":
        raise HTTPException(403, LOCAL_ONLY)


def create_app(
    service: OraqloService | None = None,
    background: bool = True,
    tailscale: TailscaleServe | None = None,
) -> FastAPI:
    """`background=False` (tests): sin scheduler; los jobs siguen corriendo en hilos."""
    service = service or OraqloService()
    runner = JobRunner(service)
    scheduler = Scheduler(service.store.settings)
    push = PushService(service.store.db, service.store.settings)
    started = time.time()

    def notify_job(job: dict) -> None:
        if job.get("kind") == "practice":
            # Corre sola cada hora: se cuentan los fallos seguidos y solo se avisa al 2.º.
            failures = 0
            if job.get("status") == "failed":
                failures = int(service.store.settings.get("practice.failures", 0)) + 1
            service.store.settings.set("practice.failures", failures)
            alert = practice_alert(job, failures)
            if alert:
                push.send(*alert, tag="practice")
            return
        note = job_notification(job)
        if note:
            push.send(*note, tag=f"job-{job.get('id')}")

    runner.listeners.append(notify_job)

    def notify_overdue() -> None:
        """Un aviso por hito vencido (una sola vez por hito)."""
        from oraqlo.goaltrack import GoalTracker as _Tracker

        notified = set(service.store.settings.get("push.notified_milestones", []))
        for goal, m in _Tracker(service.store.goals, service.store.ledger).overdue_milestones():
            key = f"{goal.id}:{m.text}:{m.target_date}"
            if key in notified:
                continue
            push.send(
                "Hito vencido",
                f"{goal.goal}: «{m.text}» era para el {m.target_date}. Márcalo o re-planifica.",
                url=f"/objetivos/{goal.id}",
                tag=f"milestone-{goal.id}",
            )
            notified.add(key)
        service.store.settings.set("push.notified_milestones", sorted(notified))

        # Predicciones que ya deberían saberse: sin resolverlas, la calibración no
        # aprende. Un aviso cuando aparecen nuevas, no uno por predicción.
        told = set(service.store.settings.get("push.notified_forecasts", []))
        due = [f for f in service.overdue_forecasts() if f.id not in told]
        if due:
            n = len(due)
            push.send(
                "Predicciones para resolver",
                f"{n} predicción{'es' if n > 1 else ''} ya debería{'n' if n > 1 else ''} "
                "saberse. Marca qué pasó para que Oraqlo aprenda.",
                url="/predicciones",
                tag="forecasts-due",
            )
            open_ids = {f.id for f in service.store.ledger.open_forecasts}
            told = (told & open_ids) | {f.id for f in due}  # sin crecer para siempre
            service.store.settings.set("push.notified_forecasts", sorted(told))

    scheduler.add(
        PeriodicTask(
            "milestones",
            lambda: timedelta(hours=6),
            lambda: push.count() > 0 or push.desktop_enabled(),
            notify_overdue,
            run_on_start=True,
        )
    )

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

    # ── auto-actualización ────────────────────────────────────────────────
    settings = service.store.settings
    cfg = service.config
    updater = Updater(cfg.home, cfg.updates.repo, cfg.updates.channel, db_path=cfg.db_path)
    managed = is_managed(updater.layout)
    if push.desktop is None and notify_supported(managed):
        push.desktop = DesktopNotifier(cfg.home)
    staging: dict[str, threading.Thread | None] = {"thread": None}

    def request_restart() -> None:
        """Pide al servidor salir con RESTART_EXIT_CODE: el lanzador arranca la versión nueva."""
        app.state.restart_requested = True
        server = getattr(app.state, "server", None)
        if server is not None:
            server.should_exit = True

    def stage_in_background() -> None:
        current = staging["thread"]
        if current is not None and current.is_alive():
            return

        def work() -> None:
            try:
                result = updater.stage(managed=managed)
                if result.status == "staged":
                    settings.set("update.staged", result.version)
                settings.set(
                    "update.last_result", {"status": result.status, "detail": result.detail}
                )
            except UpdateError as e:
                log.warning("Actualización abortada: %s", e)
                settings.set("update.last_result", {"status": "error", "detail": str(e)})
            except Exception as e:  # cualquier otro fallo debe verse, no matar el hilo en silencio
                log.exception("Actualización abortada por un error inesperado")
                settings.set(
                    "update.last_result",
                    {"status": "error", "detail": f"Error inesperado: {e}"[:300]},
                )

        thread = threading.Thread(target=work, name="oraqlo-update", daemon=True)
        staging["thread"] = thread
        thread.start()

    def check_updates() -> dict:
        try:
            result = updater.check()
        except UpdateError as e:
            settings.set("update.last_result", {"status": "error", "detail": str(e)})
            return {"status": "error", "detail": str(e)}
        settings.set("update.last_check", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        settings.set("update.available", result.version if result.status == "available" else None)
        if result.status == "available":
            if not managed:
                return {
                    "status": "unmanaged",
                    "detail": result.detail + " Esta copia es de desarrollo: actualízala con git.",
                }
            if cfg.updates.mode == "auto" or settings.get("update.approved"):
                stage_in_background()
                return {
                    "status": "staging",
                    "detail": result.detail + " Preparándola en segundo plano…",
                }
        return {"status": result.status, "detail": result.detail}

    def apply_staged() -> None:
        version = settings.get("update.staged")
        if not version or runner.active():
            return  # con trabajos en curso se pospone al siguiente tick
        if cfg.updates.mode != "auto" and not settings.get("update.approved"):
            return
        updater.activate(version, cfg.backups_dir)
        updater.layout.prune()
        settings.set("update.staged", None)
        settings.set("update.approved", False)
        settings.set("update.pending_notice", version)
        request_restart()

    def periodic_update_check() -> None:
        check_updates()

    scheduler.add(
        PeriodicTask(
            "updates",
            lambda: timedelta(hours=cfg.updates.interval_hours),
            lambda: cfg.updates.mode != "off",
            periodic_update_check,
            run_on_start=True,
        )
    )
    scheduler.add(
        PeriodicTask(
            "update-apply",
            lambda: timedelta(minutes=1),
            lambda: managed and bool(settings.get("update.staged")),
            apply_staged,
            run_on_start=True,
        )
    )

    def daily_backup() -> None:
        stamp = time.strftime("%Y%m%d")
        service.store.db.backup_to(cfg.backups_dir / f"daily-{stamp}.db")
        prune_backups(cfg.backups_dir)

    def cleanup() -> None:
        """Lo que crecería sin límite: jobs y eventos viejos, hitos ya avisados."""
        removed = service.store.db.prune_jobs(days=JOB_RETENTION_DAYS)
        if removed:
            log.info("Limpieza: %d jobs de más de %d días", removed, JOB_RETENTION_DAYS)
        notified = settings.get("push.notified_milestones", [])
        if len(notified) > 500:
            settings.set("push.notified_milestones", notified[-500:])

    scheduler.add(
        PeriodicTask(
            "cleanup", lambda: timedelta(hours=24), lambda: True, cleanup, run_on_start=True
        )
    )

    scheduler.add(
        PeriodicTask(
            "backup", lambda: timedelta(hours=24), lambda: True, daily_backup, run_on_start=True
        )
    )

    def startup_notices() -> None:
        pending = settings.get("update.pending_notice")
        if pending and pending == __version__:
            push.send(
                f"Oraqlo actualizado a {__version__}",
                "Toca para ver las novedades.",
                "/ajustes",
                tag="update",
            )
            settings.set("update.pending_notice", None)
        marker = updater.layout.root / "rolled_back.txt"
        if marker.exists():
            what = marker.read_text(encoding="utf-8").strip()
            push.send(
                "Actualización revertida",
                f"La versión nueva no arrancó ({what}). Sigues en la anterior.",
                "/ajustes",
            )
            marker.unlink(missing_ok=True)

    # ── puerto y tailnet ──────────────────────────────────────────────────
    ts = tailscale or TailscaleServe()
    app_dir = updater.layout.root

    def sync_tailscale() -> None:
        """Si el lanzador movió el puerto, `tailscale serve` debe seguirlo (la URL no
        cambia). Antes, deja la raíz de la tailnet si una versión vieja la ocupó."""
        port = getattr(app.state, "port", None)
        if not (managed and port and published_https(app_dir) is not None):
            return
        try:
            ts.migrate_from_root(app_dir, port)
            https = published_https(app_dir)
            if https is not None and https != 443:
                ts.ensure(port, https)
        except Exception:
            log.exception("No pude sincronizar tailscale serve")

    def refresh_launcher_file() -> None:
        if managed:
            try:
                refresh_launcher(updater.layout)
            except Exception:
                log.exception("No pude actualizar el lanzador")

    def replace_old_windows() -> None:
        # Tras actualizar, la ventana abierta puede ser de otra versión (y no saber
        # relanzarse sola): el servidor nuevo la cambia por la de su versión.
        time.sleep(5)  # que el servidor ya responda cuando la ventana nueva pregunte
        windows_handover.replace_old_windows(
            updater.layout.home, __version__, note=updater.layout.log
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        threading.Thread(target=sync_tailscale, name="oraqlo-tailscale", daemon=True).start()
        refresh_launcher_file()
        recovered = runner.recover()
        if recovered:
            log.warning("%d job(s) interrumpidos por reinicio marcados como fallidos", recovered)
        try:
            startup_notices()
        except Exception:
            log.exception("Avisos de arranque fallidos")
        if background:
            scheduler.start()
            if managed and windows_handover.supported():
                threading.Thread(
                    target=replace_old_windows, name="oraqlo-ventana", daemon=True
                ).start()
        yield
        scheduler.stop()
        runner.shutdown(wait=False)

    app = FastAPI(title="Oraqlo", version=__version__, lifespan=lifespan)
    app.state.service = service
    app.state.runner = runner
    app.state.scheduler = scheduler
    app.state.push = push
    app.state.notify_overdue = notify_overdue  # para los tests
    app.state.updater = updater
    app.state.restart_requested = False
    app.state.request_restart = request_restart
    app.state.check_updates = check_updates
    app.state.apply_staged = apply_staged

    @app.middleware("http")
    async def auth(request: Request, call_next):
        try:
            check_request(request)
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

    def _pending(key: str) -> str | None:
        # Tras actualizarse, lo guardado puede ser la versión que ya corre: no es novedad.
        value = settings.get(key)
        return value if isinstance(value, str) and is_newer(value, __version__) else None

    @app.get("/api/health")
    def health() -> dict:
        return {
            "app": "oraqlo",
            "status": "ok",
            "version": __version__,
            "db_version": service.store.db.version,
            "uptime_s": round(time.time() - started),
            "active_jobs": runner.active(),
            "update": {
                "available": _pending("update.available"),
                "staged": _pending("update.staged"),
                "mode": cfg.updates.mode,
                "managed": managed,
                "last_check": settings.get("update.last_check"),
                "last_result": settings.get("update.last_result"),
            },
        }

    @app.post("/api/update/check")
    def update_check() -> dict:
        return check_updates()

    @app.post("/api/update/apply", dependencies=[Depends(require_local)])
    def update_apply() -> dict:
        """Modo `notify`: el usuario aprueba; se prepara y se activa cuando no haya trabajos."""
        if not managed:
            raise HTTPException(
                409, "Esta copia no es una instalación gestionada: actualízala con git."
            )
        settings.set("update.approved", True)
        result = check_updates()
        return {
            **result,
            "detail": result["detail"] + " Se activará cuando no haya análisis en curso.",
        }

    @app.post("/api/update/rollback", dependencies=[Depends(require_local)])
    def update_rollback() -> dict:
        """Vuelve a la versión anterior instalada y reinicia."""
        if not managed:
            raise HTTPException(409, "Esta copia no es una instalación gestionada.")
        if runner.active():
            raise HTTPException(409, "Hay análisis en curso: inténtalo cuando terminen.")
        try:
            version = updater.rollback()
        except UpdateError as e:
            raise HTTPException(409, str(e)) from e
        settings.set("update.staged", None)
        settings.set("update.approved", False)
        request_restart()
        return {"version": version, "restarting": True, "detail": f"Volviendo a {version}..."}

    def tailscale_info() -> dict:
        status = ts.status() if ts.installed else {}
        published = published_https(app_dir)
        https = published or HTTPS_PORT
        return {
            "installed": ts.installed,
            "state": ts.state(status),
            "enabled": published is not None,
            "https_port": https,
            "url": ts.url(status, https) if ts.installed else None,
            "serving_port": ts.serving_port(https) if ts.installed else None,
        }

    @app.get("/api/server")
    def server_info() -> dict:
        port = getattr(app.state, "port", None)
        return {
            "port": port,
            "local_url": f"http://127.0.0.1:{port}" if port else None,
            "managed": managed,
            "tailscale": tailscale_info(),
        }

    @app.put("/api/server/tailscale", dependencies=[Depends(require_local)])
    def set_tailscale(body: TailscaleIn) -> dict:
        """Publica (o retira) Oraqlo en la tailnet para abrirlo desde el celular."""
        port = getattr(app.state, "port", None)
        if body.enabled:
            state = ts.state()
            if state == "missing":
                raise HTTPException(409, "Instala Tailscale en este PC primero.")
            if state != "running":
                raise HTTPException(409, "Abre Tailscale en este PC e inicia sesión.")
            if not port:
                raise HTTPException(409, "No sé en qué puerto corre Oraqlo.")
            if published_https(app_dir) == 443:
                ts.migrate_from_root(app_dir, port)
            https = published_https(app_dir) or ts.free_https_port(port)
            ts.ensure(port, https)
            if ts.serving_port(https) != port:
                raise HTTPException(502, "Tailscale no aceptó la publicación. Inténtalo de nuevo.")
            set_published_https(app_dir, https)
        else:
            https = published_https(app_dir)
            # Solo se retira lo que apunta a Oraqlo: la raíz u otros puertos son de otras apps.
            if https is not None and ts.serving_port(https) == port:
                ts.disable(https)
            set_published_https(app_dir, None)
        return tailscale_info()

    @app.put("/api/server/port", dependencies=[Depends(require_local)])
    def change_port(body: PortChange) -> dict:
        """Guarda el puerto nuevo y reinicia: el lanzador arranca Oraqlo en él."""
        if not managed:
            raise HTTPException(
                409,
                "Esta copia no es una instalación gestionada (copia de desarrollo).",
            )
        current = getattr(app.state, "port", None)
        if body.port == current:
            return {"port": current, "restarting": False, "detail": "Oraqlo ya usa ese puerto."}
        if port_in_use(body.port):
            raise HTTPException(
                409, f"El puerto {body.port} ya lo usa otra aplicación. Elige otro."
            )
        if runner.active():
            raise HTTPException(409, "Hay análisis en curso: cambia el puerto cuando terminen.")
        write_port_file(app_dir, body.port)
        request_restart()
        return {
            "port": body.port,
            "restarting": True,
            "local_url": f"http://127.0.0.1:{body.port}",
            "detail": f"Oraqlo se reinicia en el puerto {body.port}.",
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
            provider = service.registry.get(name)
            models = provider.list_models()
        except KeyError as e:
            raise HTTPException(404, _message(e)) from e
        except Exception as e:
            raise HTTPException(502, str(e)) from e
        # Ollama: con los modelos de la nube y si están en el PC (para elegir sin teclear).
        catalog_fn = getattr(provider, "catalog", None)
        try:
            catalog = catalog_fn() if callable(catalog_fn) else None
        except Exception:
            catalog = None
        if catalog is None:
            catalog = [{"name": m, "cloud": False, "size": None, "installed": True} for m in models]
        return {"models": models, "catalog": catalog}

    @app.post("/api/providers/{name}/test")
    def provider_test(name: str, body: ModelTestIn | None = None) -> dict:
        if name not in service.registry.names():
            raise HTTPException(404, f"Proveedor desconocido: {name}")
        if body is not None and body.model:
            return probe_model(service.registry, name, body.model)
        return probe_provider(service.registry, name).as_dict()

    @app.get("/api/ollama")
    def ollama_status() -> dict:
        return service.ollama_status()

    @app.post("/api/websearch/test")
    def websearch_test() -> dict:
        return probe_web_search().as_dict()

    # ── primer arranque y configuración desde la app ──────────────────────

    @app.get("/api/setup")
    def setup_status(request: Request) -> dict:
        ready = not service.needs_setup()
        return {
            "ready": ready,
            "needs_setup": not ready and not settings.get("setup.skipped"),
            "local": request.state.identity.via == "local",
            "legacy_imported": bool(settings.get("setup.legacy_imported")),
        }

    @app.post("/api/setup/skip")
    def setup_skip() -> dict:
        settings.set("setup.skipped", True)
        return {"ok": True}

    @app.put("/api/keys/{env}", dependencies=[Depends(require_local)])
    def put_key(env: str, body: KeyIn) -> dict:
        try:
            set_secret(cfg.home, env, body.value, service.key_envs())
        except ConfigError as e:
            raise HTTPException(400, str(e)) from e
        service.reload_config()
        return service.providers_view()

    @app.delete("/api/keys/{env}", dependencies=[Depends(require_local)])
    def delete_key(env: str) -> dict:
        try:
            set_secret(cfg.home, env, None, service.key_envs())
        except ConfigError as e:
            raise HTTPException(400, str(e)) from e
        service.reload_config()
        return service.providers_view()

    def config_view() -> dict:
        return {
            "roles": toml_roles(cfg.home),
            "updates_mode": cfg.updates.mode,
            "allowed_users": list(cfg.server.allowed_users),
        }

    @app.get("/api/config")
    def get_config() -> dict:
        return config_view()

    @app.put("/api/config", dependencies=[Depends(require_local)])
    def put_config(body: ConfigIn) -> dict:
        changes = body.model_dump(exclude_none=True)
        known = set(service.registry.names())
        for role, fields in (changes.get("roles") or {}).items():
            provider = (fields or {}).get("provider")
            if provider and provider not in known:
                raise HTTPException(400, f"Proveedor desconocido para {role}: {provider}")
        try:
            new = apply_changes(cfg.home, changes)
        except ConfigError as e:
            raise HTTPException(400, str(e)) from e
        service.reload_config(new)
        return config_view()

    # ── avisos de Windows ────────────────────────────────────────────────

    def notify_view() -> dict:
        return {
            "supported": push.desktop is not None and push.desktop.available,
            "enabled": push.desktop_enabled(),
        }

    @app.get("/api/notify/windows")
    def get_notify_windows() -> dict:
        return notify_view()

    @app.put("/api/notify/windows", dependencies=[Depends(require_local)])
    def put_notify_windows(body: ToggleIn) -> dict:
        settings.set(NOTIFY_SETTING, body.enabled)
        return notify_view()

    @app.post("/api/notify/windows/test", dependencies=[Depends(require_local)])
    def test_notify_windows() -> dict:
        if not push.desktop_enabled():
            raise HTTPException(409, "Los avisos de Windows están desactivados o no disponibles.")
        ok = push.desktop.send("Oraqlo", "Así se verán los avisos en este PC.", "/ajustes")
        if not ok:
            raise HTTPException(502, "Windows no mostró el aviso.")
        return {"ok": True}

    @app.get("/api/doctor")
    def doctor() -> dict:
        checks = [
            c.as_dict()
            for c in run_checks(service.config, service.store, ping=True, registry=service.registry)
        ]
        names = {
            "backup": "Backup diario",
            "practice": "Práctica automática",
            "updates": "Actualizaciones",
            "milestones": "Avisos de hitos",
            "cleanup": "Limpieza",
        }
        for task, err in scheduler.errors().items():
            checks.append(
                {
                    "name": names.get(task, task),
                    "ok": False,
                    "detail": f"Falló la última vez ({err['at'][:16].replace('T', ' ')}): {err['error']}",
                    "fix": "Se reintenta sola en unos minutos.",
                    "optional": False,
                }
            )
        return {"ok": all(c["ok"] or c["optional"] for c in checks), "checks": checks}

    @app.post("/api/import-legacy", dependencies=[Depends(require_local)])
    def import_legacy_files(body: LegacyIn) -> dict:
        """Importa los JSON de la versión TUI (los sube el navegador; no hay rutas)."""
        import tempfile

        from oraqlo.store.legacy import import_legacy

        files = {Path(k).name: v for k, v in body.files.items()}
        unknown = sorted(set(files) - set(LEGACY_FILES))
        if unknown or not files:
            raise HTTPException(
                400,
                f"Elige archivos {', '.join(LEGACY_FILES)} "
                f"(recibí: {', '.join(unknown) or 'ninguno'}).",
            )
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in files.items():
                try:
                    json.loads(text)
                except ValueError as e:
                    raise HTTPException(400, f"{name} no es un JSON válido.") from e
                (Path(tmp) / name).write_text(text, encoding="utf-8")
            try:
                report = import_legacy(service.store.db, tmp)
            except Exception as e:
                raise HTTPException(400, f"No pude importar: {e}") from e
        settings.set("setup.legacy_imported", True)
        return {
            "open_forecasts": report.open_forecasts,
            "resolved_forecasts": report.resolved_forecasts,
            "goals": report.goals,
            "settings": report.settings,
        }

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

    @app.post("/api/jobs/{job_id}/cancel")
    def jobs_cancel(job_id: str) -> dict:
        job = runner.cancel(job_id)
        if job is None:
            raise HTTPException(404, "Trabajo no encontrado")
        return job

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    def jobs_retry(job_id: str) -> dict:
        job_or_404(job_id)
        new_id = runner.retry(job_id)
        if new_id is None:
            raise HTTPException(409, "Solo se puede reintentar un trabajo que falló o se canceló.")
        return {"job_id": new_id}

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
                # SQLite es síncrono (y comparte cerrojo con los backups): fuera del
                # bucle de eventos, para no congelar las demás peticiones.
                events = await asyncio.to_thread(runner.events, job_id, cursor)
                for ev in events:
                    cursor = ev["seq"]
                    payload = json.dumps(ev["data"], ensure_ascii=False)
                    # "error" es el evento nativo de EventSource (cortes): se renombra.
                    kind = "job_error" if ev["type"] == "error" else ev["type"]
                    yield f"id: {ev['seq']}\nevent: {kind}\ndata: {payload}\n\n"
                job = await asyncio.to_thread(runner.get, job_id) or {}
                pending = await asyncio.to_thread(runner.events, job_id, cursor)
                if job.get("status") in TERMINAL and not pending:
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
    def runs_list(limit: int = Query(50, ge=1, le=2000)) -> list[dict]:
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

    @app.get("/api/ledger/overdue")
    def ledger_overdue() -> dict:
        return {"count": len(service.overdue_forecasts())}

    @app.get("/api/ledger.csv", include_in_schema=False)
    def ledger_csv() -> Response:
        stamp = datetime.now().strftime("%Y-%m-%d")
        return Response(
            ledger_to_csv(service.store.ledger),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="oraqlo-predicciones-{stamp}.csv"'
            },
        )

    @app.post("/api/ledger/{forecast_id}/resolve")
    def ledger_resolve(forecast_id: str, body: ResolveIn) -> dict:
        try:
            return {"resolved": service.resolve(forecast_id, body.outcome)}
        except KeyError as e:
            raise HTTPException(404, _message(e)) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.delete("/api/ledger/open/{forecast_id}", status_code=204)
    def ledger_discard(forecast_id: str) -> None:
        try:
            service.store.ledger.discard(forecast_id)
        except KeyError as e:
            raise HTTPException(404, _message(e)) from e

    @app.delete("/api/ledger/resolved/{forecast_id}", status_code=204)
    def ledger_delete_resolved(forecast_id: str) -> None:
        try:
            service.store.ledger.delete_resolved(forecast_id)
        except KeyError as e:
            raise HTTPException(404, _message(e)) from e

    @app.get("/api/calibration")
    def calibration(
        kind: Literal["prevision", "objetivo", "panel", "practica"] | None = None,
        model: str | None = None,
        days: int | None = Query(None, ge=1, le=36500),
    ) -> dict:
        return service.calibration(kind=kind, model=model or None, days=days)

    # ── objetivos ─────────────────────────────────────────────────────────

    def tracker() -> GoalTracker:
        return GoalTracker(service.store.goals, service.store.ledger)

    def goal_or_404(goal_id: str):
        try:
            return service.store.goals.get(goal_id)
        except KeyError as e:
            raise HTTPException(404, _message(e)) from e

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

    @app.patch("/api/goals/{goal_id}")
    def goals_edit(goal_id: str, body: GoalEditIn) -> dict:
        goal_or_404(goal_id)
        try:
            tracker().edit(goal_id, goal=body.goal, deadline=body.deadline)
        except GoalTrackError as e:
            raise HTTPException(422, str(e)) from e
        return goal_json(service.store.goals.get(goal_id))

    @app.patch("/api/goals/{goal_id}/milestones/{index}")
    def goals_milestone_edit(goal_id: str, index: int, body: MilestoneEditIn) -> dict:
        goal_or_404(goal_id)
        try:
            if body.text is not None or body.target_date is not None:
                tracker().edit_milestone(
                    goal_id, index, text=body.text, target_date=body.target_date
                )
            if body.status is not None:
                tracker().set_milestone(goal_id, index, body.status)
        except GoalTrackError as e:
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

    # ── notificaciones push ───────────────────────────────────────────────

    @app.get("/api/push/key")
    def push_key() -> dict:
        return {"public_key": push.public_key()}

    @app.post("/api/push/subscribe", status_code=201)
    def push_subscribe(body: PushSubIn, request: Request) -> dict:
        try:
            push.subscribe(body.model_dump(), request.state.identity.login)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return {"devices": push.count()}

    @app.post("/api/push/unsubscribe")
    def push_unsubscribe(body: EndpointIn) -> dict:
        push.unsubscribe(body.endpoint)
        return {"devices": push.count()}

    @app.post("/api/push/test")
    def push_test() -> dict:
        """Solo Web Push (los avisos de Windows tienen su propia prueba): así el
        resultado dice la verdad sobre los dispositivos."""
        sent, error = push.send_web(
            "Oraqlo", "Las notificaciones funcionan en este dispositivo.", "/ajustes", tag="test"
        )
        if not sent:
            raise HTTPException(409, error or "Ningún dispositivo recibió el aviso.")
        return {"delivered": sent}

    @app.get("/api/push/status")
    def push_status(endpoint: str = "") -> dict:
        return {
            "subscribed": bool(endpoint) and push.is_subscribed(endpoint),
            "devices": push.count(),
            "last_error": settings.get("push.last_error"),
        }

    # ── práctica ──────────────────────────────────────────────────────────

    def practice_state() -> dict:
        from oraqlo.autopractice import MARKET_SOURCE, calibration_advice, duels

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
            "recent": runner.list(limit=20, kind="practice"),
            # Pregunta a pregunta: quién se acercó más, Oraqlo o el mercado.
            "duels": duels(ledger),
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

    def index_page() -> HTMLResponse:
        # La versión va dentro del HTML: el service worker guarda este HTML junto con
        # su interfaz, así que la página sabe de qué versión es y puede notar que el
        # servidor ya es otra (ver web/src/lib/appVersion.ts).
        html = index.read_text(encoding="utf-8").replace(
            "</head>", f'<meta name="oraqlo-version" content="{escape(__version__)}" />\n</head>', 1
        )
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Ruta de API desconocida")
        target = (STATIC_DIR / path).resolve()
        if target == index.resolve():
            return index_page()
        if path and target.is_file() and STATIC_DIR.resolve() in target.parents:
            return FileResponse(target)
        if index.exists():
            return index_page()
        return PlainTextResponse(
            "Oraqlo API en marcha. El frontend no está compilado (cd web && npm run build).",
            status_code=200,
        )

    return app
