"""Ajustes persistentes clave → valor JSON (estado de la app, no configuración)."""

from __future__ import annotations

from typing import Any

from oraqlo.store.db import Database, dumps, loads


class SettingsStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, key: str, default: Any = None) -> Any:
        rows = self.db.query("SELECT value FROM settings WHERE key = ?", (key,))
        return loads(rows[0]["value"]) if rows else default

    def set(self, key: str, value: Any) -> None:
        with self.db.tx() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, dumps(value)),
            )

    def all(self) -> dict[str, Any]:
        return {r["key"]: loads(r["value"]) for r in self.db.query("SELECT * FROM settings")}
