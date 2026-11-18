"""Conversión de objetos de dominio a JSON (para la API, la base y los eventos)."""

from __future__ import annotations

import csv
import dataclasses
import io
import json
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any

from oraqlo.forecaster.base import Forecast
from oraqlo.goaltrack import TrackedGoal
from oraqlo.memory.calibration import CalibrationLedger, ResolvedForecast


def to_jsonable(obj: Any) -> Any:
    """Dataclasses → dict, timedelta → días, fechas → ISO, Enum → valor. Recursivo."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, timedelta):
        return obj.total_seconds() / 86400
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    return obj


def forecast_json(f: Forecast) -> dict:
    from oraqlo.forecaster.numeric import bins_of

    d = f.distribution
    spec = bins_of(f)
    return {
        "numeric": {"unit": spec[0], "bins": {k: list(v) for k, v in spec[1].items()}}
        if spec
        else None,
        "id": f.id,
        "question": f.question,
        "source": f.source,
        "outcomes": dict(sorted(d.outcomes.items(), key=lambda kv: -kv[1])),
        "point": d.point,
        "interval": list(d.interval) if d.interval else None,
        "interval_level": d.interval_level if d.interval else None,
        "horizon_days": f.horizon.total_seconds() / 86400,
        "assumptions": f.assumptions,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def resolved_json(r: ResolvedForecast) -> dict:
    return {
        **forecast_json(r.forecast),
        "actual_outcome": r.actual_outcome,
        "brier": r.brier,
        "resolved_at": r.resolved_at.isoformat(),
    }


def goal_json(g: TrackedGoal) -> dict:
    data = to_jsonable(g)
    data.update(
        current_p=g.current_p,
        trajectory=g.trajectory,
        days_left=g.days_left,
        next_milestone=to_jsonable(g.next_milestone),
        overdue=[i for i, m in enumerate(g.milestones) if m.overdue],
    )
    return data


CSV_COLUMNS = (
    "id",
    "pregunta",
    "fuente",
    "estado",
    "previsto",
    "prob_previsto",
    "probabilidades",
    "creada",
    "horizonte_dias",
    "resultado",
    "brier",
    "resuelta",
)


def ledger_to_csv(ledger: CalibrationLedger) -> str:
    """Todas las predicciones (abiertas y resueltas) como CSV para hoja de cálculo.

    Con BOM: así Excel abre bien los acentos. Las probabilidades van como fracción
    (0.62) con punto decimal, que es lo que esperan pandas y las hojas en inglés."""
    out = io.StringIO()
    out.write("﻿")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)

    def row(f: Forecast, status: str, r: ResolvedForecast | None = None) -> list:
        outcomes = sorted(f.distribution.outcomes.items(), key=lambda kv: -kv[1])
        top = outcomes[0] if outcomes else ("", None)
        return [
            f.id,
            f.question,
            f.source,
            status,
            top[0],
            "" if top[1] is None else round(top[1], 4),
            json.dumps(dict(outcomes), ensure_ascii=False),
            f.created_at.isoformat() if f.created_at else "",
            round(f.horizon.total_seconds() / 86400, 2),
            r.actual_outcome if r else "",
            round(r.brier, 4) if r else "",
            r.resolved_at.isoformat() if r else "",
        ]

    for f in ledger.open_forecasts:
        writer.writerow(row(f, "abierta"))
    for r in ledger.resolved_forecasts:
        writer.writerow(row(r.forecast, "resuelta", r))
    return out.getvalue()
