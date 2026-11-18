"""Operaciones sobre distribuciones de escenarios: acotar, agregar y recalibrar.

Todas reciben y devuelven `dict[etiqueta, probabilidad]` que suma 1. Son puras y
baratas: el LLMForecaster, los objetivos, el panel y la práctica las comparten.
"""

from __future__ import annotations

import math

# Nunca 0% ni 100% (ADR 0001): una certeza equivocada da el peor Brier posible y
# una log-pérdida infinita. Con dos escenarios el rango queda en [2%, 98%].
BINARY_FLOOR = 0.02
MULTI_FLOOR = 0.01


def floor_for(n: int) -> float:
    return BINARY_FLOOR if n <= 2 else MULTI_FLOOR


def clamp_probability(p: float, floor: float = BINARY_FLOOR) -> float:
    """Una probabilidad suelta (p. ej. P(éxito) de un objetivo) dentro de [floor, 1-floor]."""
    return min(max(float(p), floor), 1.0 - floor)


def clamp(outcomes: dict[str, float], floor: float | None = None) -> dict[str, float]:
    """Sube a `floor` los escenarios por debajo y reparte el resto en proporción.

    Si ninguno está por debajo, devuelve la distribución intacta (solo normalizada):
    no se toca lo que ya es razonable.
    """
    total = sum(outcomes.values())
    if not outcomes or total <= 0:
        raise ValueError("Distribución vacía o sin probabilidad")
    probs = {k: max(float(v), 0.0) / total for k, v in outcomes.items()}
    n = len(probs)
    if n < 2:
        return probs
    floor = floor_for(n) if floor is None else floor
    floor = min(floor, 1.0 / n)
    low = {k for k, p in probs.items() if p < floor}
    if not low:
        return probs
    # Los que quedan por encima pueden caer por debajo al reescalar: iterar.
    while True:
        rest = 1.0 - floor * len(low)
        high_total = sum(p for k, p in probs.items() if k not in low)
        scaled = {k: p / high_total * rest for k, p in probs.items() if k not in low}
        newly_low = {k for k, p in scaled.items() if p < floor}
        if not newly_low:
            return {k: (floor if k in low else scaled[k]) for k in probs}
        low |= newly_low


def pool_log_odds(
    samples: list[dict[str, float]], weights: list[float] | None = None
) -> dict[str, float]:
    """Combina varias distribuciones sobre los MISMOS escenarios.

    Media geométrica normalizada (equivale a promediar log-odds en el caso binario):
    a diferencia de la media aritmética, no arrastra hacia el 50% cuando las
    muestras coinciden, y una sola muestra extrema pesa menos que en un máximo.
    `weights` (opcional, uno por muestra) da más voz a quien acierta más.
    """
    if not samples:
        raise ValueError("Sin muestras que agregar")
    labels = list(samples[0])
    if any(set(s) != set(labels) for s in samples[1:]):
        raise ValueError("Las muestras no comparten los mismos escenarios")
    if weights is not None and (len(weights) != len(samples) or min(weights) < 0):
        raise ValueError("Hace falta un peso no negativo por muestra")
    if len(samples) == 1:
        return clamp(samples[0])
    w = weights or [1.0] * len(samples)
    total_w = sum(w)
    if total_w <= 0:
        raise ValueError("Los pesos suman cero")
    safe = [clamp(s) for s in samples]  # sin ceros: log(0) no existe
    logs = {
        k: sum(wi * math.log(s[k]) for wi, s in zip(w, safe, strict=True)) / total_w for k in labels
    }
    top = max(logs.values())
    weights = {k: math.exp(v - top) for k, v in logs.items()}
    total = sum(weights.values())
    return {k: w / total for k, w in weights.items()}


def power(outcomes: dict[str, float], a: float) -> dict[str, float]:
    """Recalibración por potencia: p'ₖ ∝ pₖ^a.

    a < 1 suaviza (corrige sobreconfianza), a > 1 concentra (corrige subconfianza),
    a = 1 no cambia nada. Mantiene el orden de los escenarios.
    """
    if a == 1.0:
        return dict(outcomes)
    safe = clamp(outcomes)
    weights = {k: p**a for k, p in safe.items()}
    total = sum(weights.values())
    return {k: w / total for k, w in weights.items()}


# Sin datos concretos (ni investigación ni observaciones), la seguridad del modelo
# es intuición: en la práctica real, con dos tenistas que no conocía daba 70% y
# fallaba con Brier ~0.9. Se suaviza hacia el reparto igual antes de recalibrar.
NO_DATA_FACTOR = 0.8
NO_DATA_NOTE = f"Sin datos concretos: probabilidades suavizadas hacia el reparto igual (factor {NO_DATA_FACTOR})."
NO_DATA_BASE_NOTE = (
    f"Sin datos concretos: probabilidades acercadas a la tasa base (factor {NO_DATA_FACTOR})."
)


def temper_without_data(
    outcomes: dict[str, float], base: dict[str, float] | None = None
) -> dict[str, float]:
    """Suaviza una estimación sin datos: p' ∝ p^0.8 · base^0.2.

    Sin tasa base (o si no cubre los mismos escenarios) la base es el reparto igual,
    que es justo `power(p, 0.8)`."""
    if not base or set(base) != set(outcomes) or sum(base.values()) <= 0:
        return clamp(power(outcomes, NO_DATA_FACTOR))
    return clamp(pool_log_odds([outcomes, base], [NO_DATA_FACTOR, 1.0 - NO_DATA_FACTOR]))


def parse_base_rate(raw: object, labels: list[str]) -> dict[str, float] | None:
    """La tasa base numérica que da el modelo (`base_rate_outcomes`), sobre las mismas
    etiquetas que la previsión; None si falta o no es utilizable."""
    if not isinstance(raw, dict):
        return None
    by_norm = {" ".join(str(k).split()).casefold(): v for k, v in raw.items()}
    try:
        values = {k: float(by_norm[" ".join(k.split()).casefold()]) for k in labels}
    except (KeyError, TypeError, ValueError):
        return None
    total = sum(values.values())
    if total <= 0 or any(v < 0 for v in values.values()):
        return None
    return {k: v / total for k, v in values.items()}
