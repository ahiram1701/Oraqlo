"""QuantForecaster: el oráculo cuantitativo para variables numéricas con historia.

Modelo: suavizado exponencial de Holt (nivel + tendencia), con α y β elegidos
por mínimo error cuadrático de un paso. El intervalo de predicción sale de la
dispersión de esos errores y se ensancha con √h (más horizonte, más
incertidumbre — invariante del oráculo honesto).

Dos modos:
- Sin umbral: Distribution con `point` + `interval` (al nivel pedido).
- Con umbral: Distribution binaria {"> u", "≤ u"} vía la normal predictiva.
  Es la forma *resoluble* en el ledger: cuando llegue el dato, se sabe cuál pasó.
"""

from __future__ import annotations

import itertools
import math
import uuid
from datetime import UTC, datetime, timedelta
from statistics import NormalDist, median

from oraqlo.forecaster.base import Distribution, Forecast, Forecaster
from oraqlo.world_model import WorldModel, WorldState

_GRID = [i / 10 for i in range(1, 10)]


def holt_fit(values: list[float]) -> tuple[float, float, float, float, float]:
    """Ajusta Holt por rejilla. Devuelve (nivel, tendencia, α, β, σ de errores a 1 paso)."""
    if len(values) < 3:
        raise ValueError("Se necesitan al menos 3 observaciones para ajustar una tendencia")
    best: tuple[float, float, float, float, float] | None = None
    best_sse = math.inf
    for alpha in _GRID:
        for beta in _GRID:
            level, trend = values[0], values[1] - values[0]
            sse = 0.0
            for y in values[1:]:
                pred = level + trend
                sse += (y - pred) ** 2
                new_level = alpha * y + (1 - alpha) * pred
                trend = beta * (new_level - level) + (1 - beta) * trend
                level = new_level
            if sse < best_sse:
                best_sse = sse
                sigma = math.sqrt(sse / max(len(values) - 2, 1))
                best = (level, trend, alpha, beta, sigma)
    assert best is not None
    return best


def series_from_history(
    model: WorldModel, entity_id: str, variable: str
) -> list[tuple[datetime, float]]:
    """Serie temporal (timestamp, valor numérico) de una variable a lo largo del historial."""
    out: list[tuple[datetime, float]] = []
    seen: set[datetime] = set()
    for state in model._history:  # el historial es la fuente de verdad versionada
        entity = state.entities.get(entity_id)
        var = entity.variables.get(variable) if entity else None
        if var is None or var.updated_at in seen:
            continue
        try:
            value = float(var.value)
        except (TypeError, ValueError):
            continue
        seen.add(var.updated_at)
        out.append((var.updated_at, value))
    return sorted(out)


class QuantForecaster(Forecaster):
    def __init__(
        self,
        world_model: WorldModel,
        entity_id: str,
        variable: str,
        threshold: float | None = None,
        level: float = 0.9,
        source: str = "quant:holt",
    ) -> None:
        self.world_model = world_model
        self.entity_id = entity_id
        self.variable = variable
        self.threshold = threshold
        self.level = level
        self.source = source

    def forecast(self, state: WorldState, question: str, horizon: timedelta) -> Forecast:
        series = series_from_history(self.world_model, self.entity_id, self.variable)
        if len(series) < 3:
            raise ValueError(
                f"{self.entity_id}.{self.variable}: {len(series)} observaciones numéricas; "
                f"se necesitan al menos 3."
            )
        times = [t for t, _ in series]
        values = [v for _, v in series]
        step = median((b - a).total_seconds() for a, b in itertools.pairwise(times))
        steps = max(1.0, horizon.total_seconds() / step) if step > 0 else 1.0

        level, trend, alpha, beta, sigma = holt_fit(values)
        point = level + trend * steps
        spread = max(sigma, 1e-9) * math.sqrt(steps)
        z = NormalDist().inv_cdf(0.5 + self.level / 2)
        assumptions = [
            f"Holt α={alpha:.1f} β={beta:.1f} sobre {len(values)} observaciones",
            f"La tendencia reciente ({trend:+.3g} por paso) continúa {steps:.1f} pasos",
            "Errores aproximadamente normales e independientes",
        ]

        if self.threshold is None:
            distribution = Distribution(
                point=point,
                interval=(point - z * spread, point + z * spread),
                interval_level=self.level,
            )
        else:
            p_above = 1.0 - NormalDist(point, spread).cdf(self.threshold)
            p_above = min(max(p_above, 0.001), 0.999)  # nunca certeza absoluta
            u = f"{self.threshold:g}"
            distribution = Distribution(outcomes={f"> {u}": p_above, f"≤ {u}": 1.0 - p_above})
        return Forecast(
            id=str(uuid.uuid4()),
            question=question,
            distribution=distribution,
            horizon=horizon,
            assumptions=assumptions,
            source=self.source,
            created_at=datetime.now(UTC),
        )
