"""EnsembleForecaster: combina oráculos ponderando por su historial de aciertos.

Peso de cada miembro = 1 / (Brier medio + ε), con Brier tomado de
`CalibrationLedger.reliability(source)`. Un miembro sin historial recibe el
Brier "previo" (0.25: el de una moneda al aire) — ni premio ni castigo hasta
que demuestre algo.

Las predicciones de cada miembro se registran en el ledger con id
`<id-del-ensamble>::<fuente>`; `resolve_group()` las cierra todas a la vez, así
cada fuente acumula su propia fiabilidad y los pesos aprenden con el uso.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from oraqlo.forecaster.base import Distribution, Forecast, Forecaster
from oraqlo.memory.calibration import CalibrationLedger, normalize_label
from oraqlo.world_model import WorldState

MEMBER_SEP = "::"


class EnsembleForecaster(Forecaster):
    def __init__(
        self,
        members: list[Forecaster],
        ledger: CalibrationLedger,
        source: str = "ensemble",
        prior_brier: float = 0.25,
        eps: float = 0.02,
        record_members: bool = True,
    ) -> None:
        if not members:
            raise ValueError("El ensamble necesita al menos un miembro")
        self.members = members
        self.ledger = ledger
        self.source = source
        self.prior_brier = prior_brier
        self.eps = eps
        self.record_members = record_members

    def weight(self, source: str) -> float:
        brier = self.ledger.reliability(source)
        return 1.0 / ((self.prior_brier if brier is None else brier) + self.eps)

    def forecast(self, state: WorldState, question: str, horizon: timedelta) -> Forecast:
        ensemble_id = str(uuid.uuid4())
        member_forecasts = [m.forecast(state, question, horizon) for m in self.members]

        combined: dict[str, float] = {}
        display: dict[str, str] = {}  # etiqueta normalizada → la primera forma vista
        total_weight = 0.0
        assumptions: list[str] = []
        for f in member_forecasts:
            if not f.distribution.outcomes:
                raise ValueError(
                    f"El miembro {f.source!r} devolvió un intervalo: el ensamble combina escenarios."
                )
            w = self.weight(f.source)
            total_weight += w
            for label, p in f.distribution.outcomes.items():
                key = normalize_label(label)
                display.setdefault(key, label)
                combined[key] = combined.get(key, 0.0) + w * p
            assumptions.extend(f"[{f.source}] {a}" for a in f.assumptions)

        outcomes = {display[k]: v / total_weight for k, v in combined.items()}
        norm = sum(outcomes.values())
        outcomes = {k: v / norm for k, v in outcomes.items()}

        if self.record_members:
            for f in member_forecasts:
                f.id = f"{ensemble_id}{MEMBER_SEP}{f.source}"
                self.ledger.record(f)

        weights = ", ".join(
            f"{f.source}={self.weight(f.source) / total_weight:.0%}" for f in member_forecasts
        )
        return Forecast(
            id=ensemble_id,
            question=question,
            distribution=Distribution(outcomes=outcomes),
            horizon=horizon,
            assumptions=[f"Ensamble ponderado por fiabilidad: {weights}", *assumptions],
            source=self.source,
            created_at=datetime.now(UTC),
        )


def resolve_group(ledger: CalibrationLedger, forecast_id: str, actual_outcome: str) -> int:
    """Resuelve una predicción y las de sus miembros (`<id>::<fuente>`). Devuelve cuántas cerró."""
    ids = [forecast_id] + [
        f.id for f in ledger.open_forecasts if f.id.startswith(forecast_id + MEMBER_SEP)
    ]
    for fid in ids:
        ledger.resolve(fid, actual_outcome)
    return len(ids)
