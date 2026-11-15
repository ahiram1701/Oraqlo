"""Importación de los JSON de la versión TUI (oraqlo_ledger/goals/settings.json).

Idempotente: importar dos veces no duplica nada. Conserva los Brier ya
calculados tal cual (no se re-puntúa nada: la historia de calibración es sagrada).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from oraqlo.goaltrack import GoalStore
from oraqlo.memory.store import JsonLedger
from oraqlo.store.db import Database
from oraqlo.store.goals import SqliteGoalStore
from oraqlo.store.ledger import SqliteLedger
from oraqlo.store.settings import SettingsStore


@dataclass
class ImportReport:
    open_forecasts: int = 0
    resolved_forecasts: int = 0
    goals: int = 0
    settings: int = 0
    missing: list[str] = field(default_factory=list)


def import_legacy(db: Database, directory: str | Path) -> ImportReport:
    directory = Path(directory)
    report = ImportReport()
    ledger = SqliteLedger(db)

    ledger_file = directory / "oraqlo_ledger.json"
    if ledger_file.exists():
        legacy = JsonLedger(ledger_file)
        for f in legacy.open_forecasts:
            if f.id not in {x.id for x in ledger.open_forecasts}:
                ledger.record(f)
                report.open_forecasts += 1
        known = {r.forecast.id for r in ledger.resolved_forecasts}
        for r in legacy.resolved_forecasts:
            if r.forecast.id not in known:
                ledger.import_resolved(r)
                report.resolved_forecasts += 1
    else:
        report.missing.append(ledger_file.name)

    goals_file = directory / "oraqlo_goals.json"
    if goals_file.exists():
        store = SqliteGoalStore(db)
        existing = {g.id for g in store.list_all()}
        for goal in GoalStore(goals_file).list_all():
            if goal.id not in existing:
                store.add(goal)
                report.goals += 1
    else:
        report.missing.append(goals_file.name)

    settings_file = directory / "oraqlo_settings.json"
    if settings_file.exists():
        settings = SettingsStore(db)
        for key, value in json.loads(settings_file.read_text(encoding="utf-8")).items():
            if settings.get(key) is None:
                settings.set(key, value)
                report.settings += 1
    else:
        report.missing.append(settings_file.name)
    return report
