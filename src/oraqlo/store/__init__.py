"""Persistencia de Oraqlo en SQLite (un único archivo en el directorio de datos)."""

from __future__ import annotations

from pathlib import Path

from .db import Database
from .goals import SqliteGoalStore
from .ledger import SqliteLedger
from .settings import SettingsStore


class Store:
    """Punto de acceso único al estado persistente."""

    def __init__(self, path: str | Path) -> None:
        self.db = Database(path)
        self.ledger = SqliteLedger(self.db)
        self.goals = SqliteGoalStore(self.db)
        self.settings = SettingsStore(self.db)

    def close(self) -> None:
        self.db.close()


__all__ = ["Database", "SettingsStore", "SqliteGoalStore", "SqliteLedger", "Store"]
