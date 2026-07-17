"""Memoria + calibración: el bucle de aprendizaje del oráculo.

Registra cada predicción; cuando llega el resultado real la puntúa (Brier /
log-loss) por fuente, y esos históricos alimentan los pesos del ensamble y la
recalibración. Sin cerrar predicciones con resultados, el sistema no aprende.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from oraqlo.forecaster.base import Forecast


def brier_score(predicted_p: float, outcome: bool) -> float:
    """Brier score de un evento binario: (p − o)². 0 = perfecto; 0.25 = azar con p=0.5."""
    if not 0.0 <= predicted_p <= 1.0:
        raise ValueError(f"Probabilidad fuera de [0,1]: {predicted_p}")
    return (predicted_p - (1.0 if outcome else 0.0)) ** 2


def log_loss(predicted_p: float, outcome: bool, eps: float = 1e-12) -> float:
    """Log-loss (entropía cruzada) de un evento binario. Penaliza fuerte la sobreconfianza."""
    if not 0.0 <= predicted_p <= 1.0:
        raise ValueError(f"Probabilidad fuera de [0,1]: {predicted_p}")
    p = min(max(predicted_p, eps), 1.0 - eps)
    return -(math.log(p) if outcome else math.log(1.0 - p))


def multiclass_brier(outcomes: dict[str, float], actual: str) -> float:
    """Brier multiclase: Σ_k (p_k − 1[k=actual])².

    Si `actual` no está entre los outcomes predichos, cuenta como p=0 para el
    resultado real (la peor sorpresa posible: el oráculo ni lo contempló).
    Rango: 0 (perfecto) a 2 (certeza total en el outcome equivocado).
    """
    score = sum((p - (1.0 if label == actual else 0.0)) ** 2 for label, p in outcomes.items())
    if actual not in outcomes:
        score += 1.0  # (0 − 1)² por el outcome real no contemplado
    return score


@dataclass
class ResolvedForecast:
    """Predicción cerrada: lo que se predijo + lo que realmente pasó."""

    forecast: Forecast
    actual_outcome: str
    brier: float
    resolved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class CalibrationLedger:
    """Libro mayor de predicciones: abiertas hasta que llega el resultado real.

    - `record(forecast)` registra una predicción abierta (idempotente por id).
    - `resolve(forecast_id, actual_outcome)` la cierra con Brier multiclase.
    - `reliability(source)` es el Brier medio de una fuente — el peso que usa
      el EnsembleForecaster para ponderar fuentes (menor = más fiable).
    """

    def __init__(self) -> None:
        self._open: dict[str, Forecast] = {}
        self._resolved: list[ResolvedForecast] = []

    def record(self, forecast: Forecast) -> None:
        forecast.distribution.validate()
        self._open[forecast.id] = forecast

    def resolve(self, forecast_id: str, actual_outcome: str) -> ResolvedForecast:
        """Cierra la predicción `forecast_id` con el resultado real observado."""
        forecast = self._open.pop(forecast_id, None)
        if forecast is None:
            raise KeyError(
                f"No hay predicción abierta con id '{forecast_id}'. "
                f"Abiertas: {sorted(self._open)}"
            )
        if not forecast.distribution.outcomes:
            raise ValueError(
                f"La predicción '{forecast_id}' es numérica (intervalo), no de escenarios; "
                f"la resolución de intervalos llegará con el QuantForecaster."
            )
        resolved = ResolvedForecast(
            forecast=forecast,
            actual_outcome=actual_outcome,
            brier=multiclass_brier(forecast.distribution.outcomes, actual_outcome),
        )
        self._resolved.append(resolved)
        return resolved

    def discard(self, forecast_id: str) -> Forecast:
        """Elimina una predicción abierta SIN puntuarla (no afecta a la calibración).

        Distinto de resolve: descartar es "esta predicción ya no interesa";
        resolver es "esto fue lo que pasó".
        """
        forecast = self._open.pop(forecast_id, None)
        if forecast is None:
            raise KeyError(
                f"No hay predicción abierta con id '{forecast_id}'. "
                f"Abiertas: {sorted(self._open)}"
            )
        return forecast

    def delete_resolved(self, forecast_id: str) -> ResolvedForecast:
        """Elimina una predicción resuelta. OJO: borra historial de calibración —
        la fiabilidad de su fuente se recalcula sin ella."""
        for i, resolved in enumerate(self._resolved):
            if resolved.forecast.id == forecast_id:
                return self._resolved.pop(i)
        raise KeyError(f"No hay predicción resuelta con id '{forecast_id}'.")

    def reliability(self, source: str) -> float | None:
        """Brier medio histórico de `source` (None si no tiene predicciones cerradas)."""
        scores = [r.brier for r in self._resolved if r.forecast.source == source]
        if not scores:
            return None
        return sum(scores) / len(scores)

    @property
    def open_forecasts(self) -> list[Forecast]:
        return list(self._open.values())

    @property
    def resolved_forecasts(self) -> list[ResolvedForecast]:
        return list(self._resolved)
