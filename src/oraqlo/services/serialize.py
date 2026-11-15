"""Conversión de objetos de dominio a JSON (para la API, la base y los eventos)."""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any

from oraqlo.forecaster.base import Forecast
from oraqlo.goaltrack import TrackedGoal
from oraqlo.memory.calibration import ResolvedForecast


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
    d = f.distribution
    return {
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
