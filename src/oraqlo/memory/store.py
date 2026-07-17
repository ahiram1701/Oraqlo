"""Persistencia del ledger de calibración a JSON.

Sin esto, las predicciones mueren con el proceso y el bucle de aprendizaje no
existe entre sesiones. JsonLedger guarda tras cada record/resolve (el archivo
completo: los volúmenes esperados son de decenas de predicciones, no millones).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.memory.calibration import CalibrationLedger, ResolvedForecast


def _forecast_to_dict(f: Forecast) -> dict:
    return {
        "id": f.id,
        "question": f.question,
        "outcomes": f.distribution.outcomes,
        "horizon_days": f.horizon.total_seconds() / 86400,
        "assumptions": f.assumptions,
        "source": f.source,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def _forecast_from_dict(data: dict) -> Forecast:
    return Forecast(
        id=data["id"],
        question=data["question"],
        distribution=Distribution(outcomes={str(k): float(v) for k, v in data["outcomes"].items()}),
        horizon=timedelta(days=data["horizon_days"]),
        assumptions=list(data.get("assumptions", [])),
        source=data.get("source", "unknown"),
        created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
    )


class JsonLedger(CalibrationLedger):
    """CalibrationLedger que persiste a un archivo JSON tras cada cambio."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for item in data.get("open", []):
            super().record(_forecast_from_dict(item))
        for item in data.get("resolved", []):
            self._resolved.append(
                ResolvedForecast(
                    forecast=_forecast_from_dict(item["forecast"]),
                    actual_outcome=item["actual_outcome"],
                    brier=float(item["brier"]),
                    resolved_at=datetime.fromisoformat(item["resolved_at"]),
                )
            )

    def _save(self) -> None:
        payload = {
            "open": [_forecast_to_dict(f) for f in self._open.values()],
            "resolved": [
                {
                    "forecast": _forecast_to_dict(r.forecast),
                    "actual_outcome": r.actual_outcome,
                    "brier": r.brier,
                    "resolved_at": r.resolved_at.isoformat(),
                }
                for r in self._resolved
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def record(self, forecast: Forecast) -> None:
        super().record(forecast)
        self._save()

    def resolve(self, forecast_id: str, actual_outcome: str) -> ResolvedForecast:
        resolved = super().resolve(forecast_id, actual_outcome)
        self._save()
        return resolved

    def discard(self, forecast_id: str) -> Forecast:
        forecast = super().discard(forecast_id)
        self._save()
        return forecast

    def delete_resolved(self, forecast_id: str) -> ResolvedForecast:
        resolved = super().delete_resolved(forecast_id)
        self._save()
        return resolved
