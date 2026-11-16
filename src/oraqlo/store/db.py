"""SQLite: el único dueño del estado de Oraqlo.

- WAL + busy_timeout: lecturas concurrentes y escrituras serializadas sin
  "database is locked" espurios.
- Una conexión por `Database` compartida entre hilos, protegida por un RLock:
  el servidor es un único proceso y las escrituras son pequeñas.
- Migraciones versionadas con `PRAGMA user_version` (sin dependencias extra):
  cada entrada de MIGRATIONS se aplica una vez, en orden, en una transacción.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

MIGRATIONS: list[str] = [
    # v1 — esquema inicial
    """
    CREATE TABLE forecasts (
        id             TEXT PRIMARY KEY,
        question       TEXT NOT NULL,
        source         TEXT NOT NULL,
        outcomes       TEXT NOT NULL,          -- JSON {label: p}
        horizon_days   REAL NOT NULL,
        assumptions    TEXT NOT NULL DEFAULT '[]',
        created_at     TEXT,
        status         TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
        actual_outcome TEXT,
        brier          REAL,
        resolved_at    TEXT,
        seq            INTEGER                 -- orden estable de resolución
    );
    CREATE INDEX forecasts_status ON forecasts(status);
    CREATE INDEX forecasts_source ON forecasts(source);

    CREATE TABLE goals (
        id         TEXT PRIMARY KEY,
        status     TEXT NOT NULL,
        data       TEXT NOT NULL,             -- JSON de TrackedGoal
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL                    -- JSON
    );

    CREATE TABLE cases (
        id         TEXT PRIMARY KEY,
        name       TEXT NOT NULL,
        data       TEXT NOT NULL,             -- JSON del caso
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE runs (
        id         TEXT PRIMARY KEY,
        kind       TEXT NOT NULL,             -- forecast | cycle | goal | practice
        title      TEXT NOT NULL,
        data       TEXT NOT NULL,             -- JSON del resultado completo
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE jobs (
        id          TEXT PRIMARY KEY,
        kind        TEXT NOT NULL,
        status      TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
        params      TEXT NOT NULL DEFAULT '{}',
        result      TEXT,
        error       TEXT,
        run_id      TEXT,
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        started_at  TEXT,
        finished_at TEXT
    );
    CREATE INDEX jobs_status ON jobs(status);

    CREATE TABLE job_events (
        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        seq    INTEGER NOT NULL,
        at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        type   TEXT NOT NULL,
        data   TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (job_id, seq)
    );

    CREATE TABLE push_subscriptions (
        endpoint   TEXT PRIMARY KEY,
        data       TEXT NOT NULL,
        user_login TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
]


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(
            str(path), check_same_thread=False, isolation_level=None, timeout=10.0
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 10000")
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.migrate()

    @property
    def version(self) -> int:
        return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self) -> int:
        """Aplica las migraciones pendientes. Devuelve cuántas aplicó."""
        applied = 0
        with self.lock:
            current = self.version
            if current > len(MIGRATIONS):
                raise RuntimeError(
                    f"La base de datos es de una versión más nueva de Oraqlo "
                    f"(esquema v{current}, esta versión conoce hasta v{len(MIGRATIONS)})."
                )
            for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
                # executescript hace COMMIT implícito: se envuelve a mano. Si falla a
                # medias se deshace; si no, la conexión quedaría dentro de la transacción.
                try:
                    self.conn.executescript(
                        f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;"
                    )
                except Exception:
                    if self.conn.in_transaction:
                        self.conn.execute("ROLLBACK")
                    raise
                applied += 1
        return applied

    def prune_jobs(self, days: int) -> int:
        """Borra los jobs terminados hace más de `days` días (y sus eventos)."""
        cutoff = f"-{int(days)} days"
        old = (
            "SELECT id FROM jobs WHERE status IN ('done', 'failed', 'cancelled') "
            "AND COALESCE(finished_at, created_at) < datetime('now', ?)"
        )
        with self.tx() as conn:
            conn.execute(f"DELETE FROM job_events WHERE job_id IN ({old})", (cutoff,))
            return conn.execute(f"DELETE FROM jobs WHERE id IN ({old})", (cutoff,)).rowcount

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Transacción explícita (con el lock tomado): todo o nada."""
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def backup_to(self, dest: str | Path) -> Path:
        """Copia consistente en caliente (API de backup de SQLite)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            target = sqlite3.connect(str(dest))
            try:
                self.conn.backup(target)
            finally:
                target.close()
        return dest

    def close(self) -> None:
        with self.lock:
            self.conn.close()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(text: str | None) -> Any:
    return json.loads(text) if text else None
