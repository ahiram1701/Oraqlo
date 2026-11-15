"""JobRunner: trabajos largos en segundo plano, persistidos en SQLite.

Un análisis con panel tarda minutos; en el celular la pantalla se bloquea y la
conexión se corta. Por eso todo flujo largo es un *job*:
- su estado (queued → running → done/failed) vive en la tabla `jobs`;
- cada evento de progreso se guarda en `job_events` con número de secuencia,
  así un cliente que se reconecta pide "desde el evento N" y no pierde nada;
- el resultado final queda en `jobs.result` (y el informe en `runs`).

Al arrancar, los jobs que quedaron a medias (el proceso murió) se marcan como
fallidos con un motivo claro, en vez de quedarse "corriendo" para siempre.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from oraqlo.services.core import OraqloService
from oraqlo.services.power import keep_awake
from oraqlo.store.db import dumps, loads

log = logging.getLogger(__name__)

TERMINAL = ("done", "failed", "cancelled")


def _now() -> str:
    return datetime.now(UTC).isoformat()


class JobRunner:
    def __init__(self, service: OraqloService, workers: int = 2) -> None:
        self.service = service
        self.db = service.store.db
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="oraqlo-job")
        self._seq: dict[str, int] = {}
        self._lock = threading.Lock()
        self.listeners: list[Callable[[dict], None]] = []  # se llaman al terminar un job
        self.handlers: dict[str, Callable[..., dict]] = {
            "forecast": service.ask_forecast,
            "full": service.ask_full,
            "goal": service.achieve_goal,
            "case": service.run_case,
            "practice": service.practice,
            "checkin": service.goal_checkin,
            "replan": service.goal_replan,
        }

    # ── ciclo de vida ─────────────────────────────────────────────────────

    def recover(self) -> int:
        """Marca como fallidos los jobs interrumpidos por un reinicio."""
        with self.db.tx() as conn:
            cur = conn.execute(
                "UPDATE jobs SET status = 'failed', finished_at = ?, "
                "error = 'Interrumpido: Oraqlo se reinició mientras corría.' "
                "WHERE status IN ('queued', 'running')",
                (_now(),),
            )
            return cur.rowcount

    def shutdown(self, wait: bool = False) -> None:
        self.pool.shutdown(wait=wait, cancel_futures=True)

    # ── API ───────────────────────────────────────────────────────────────

    def submit(self, kind: str, params: dict[str, Any] | None = None) -> str:
        if kind not in self.handlers:
            raise ValueError(f"Tipo de job desconocido: {kind!r}")
        params = params or {}
        job_id = str(uuid.uuid4())
        with self.db.tx() as conn:
            conn.execute(
                "INSERT INTO jobs (id, kind, status, params) VALUES (?, ?, 'queued', ?)",
                (job_id, kind, dumps(params)),
            )
        self.pool.submit(self._run, job_id, kind, params)
        return job_id

    def get(self, job_id: str) -> dict | None:
        rows = self.db.query("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return self._row(rows[0]) if rows else None

    def list(self, limit: int = 30, kind: str | None = None) -> list[dict]:
        if kind:
            rows = self.db.query(
                "SELECT * FROM jobs WHERE kind = ? ORDER BY created_at DESC LIMIT ?", (kind, limit)
            )
        else:
            rows = self.db.query("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._row(r, with_result=False) for r in rows]

    def active(self, kind: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running')"
        params: tuple = ()
        if kind:
            sql += " AND kind = ?"
            params = (kind,)
        return int(self.db.query(sql, params)[0][0])

    def events(self, job_id: str, after: int = 0) -> list[dict]:
        rows = self.db.query(
            "SELECT seq, at, type, data FROM job_events WHERE job_id = ? AND seq > ? ORDER BY seq",
            (job_id, after),
        )
        return [
            {"seq": r["seq"], "at": r["at"], "type": r["type"], "data": loads(r["data"])}
            for r in rows
        ]

    # ── interno ───────────────────────────────────────────────────────────

    @staticmethod
    def _row(r, with_result: bool = True) -> dict:
        data = {
            "id": r["id"],
            "kind": r["kind"],
            "status": r["status"],
            "params": loads(r["params"]),
            "error": r["error"],
            "run_id": r["run_id"],
            "created_at": r["created_at"],
            "started_at": r["started_at"],
            "finished_at": r["finished_at"],
        }
        if with_result:
            data["result"] = loads(r["result"])
        return data

    def _emit(self, job_id: str, kind: str, data: dict[str, Any]) -> None:
        with self._lock:
            seq = self._seq.get(job_id, 0) + 1
            self._seq[job_id] = seq
        with self.db.tx() as conn:
            conn.execute(
                "INSERT INTO job_events (job_id, seq, type, data) VALUES (?, ?, ?, ?)",
                (job_id, seq, kind, dumps(data)),
            )

    def _run(self, job_id: str, kind: str, params: dict[str, Any]) -> None:
        keep_awake.acquire()  # que el PC no se suspenda a mitad de un análisis
        try:
            self._execute(job_id, kind, params)
        finally:
            keep_awake.release()

    def _execute(self, job_id: str, kind: str, params: dict[str, Any]) -> None:
        with self.db.tx() as conn:
            conn.execute(
                "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?", (_now(), job_id)
            )
        emit = lambda k, d: self._emit(job_id, k, d)
        try:
            result = self.handlers[kind](**params, emit=emit)
        except Exception as e:  # el fallo de un job se reporta, nunca tumba el servidor
            log.exception("Job %s (%s) falló", job_id, kind)
            message = f"{type(e).__name__}: {e}" if not str(e) else str(e)
            emit("error", {"msg": message})
            with self.db.tx() as conn:
                conn.execute(
                    "UPDATE jobs SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                    (message, _now(), job_id),
                )
        else:
            with self.db.tx() as conn:
                conn.execute(
                    "UPDATE jobs SET status = 'done', result = ?, run_id = ?, finished_at = ? "
                    "WHERE id = ?",
                    (dumps(result), result.get("run_id"), _now(), job_id),
                )
        finally:
            with self._lock:
                self._seq.pop(job_id, None)
        job = self.get(job_id)
        for listener in self.listeners:
            try:
                listener(job or {})
            except Exception:
                log.exception("Listener de job falló")
