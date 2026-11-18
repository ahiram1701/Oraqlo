"""Preguntas de cantidad («¿cuánto…?», «¿cuántos…?»): escenarios por intervalos.

En vez de un número suelto (una certeza disfrazada), el oráculo reparte la
probabilidad entre intervalos contiguos que cubren todo («menos de 10», «entre 10 y
20», «20 o más»). Así la previsión se puntúa con el mismo Brier que las demás, sin
cambiar el ledger. Los límites viajan en un supuesto con formato fijo (como la nota
de recalibración), de modo que al resolver basta con escribir el número que salió.
"""

from __future__ import annotations

import itertools
import json
import math

from oraqlo.forecaster.base import Forecast

NUMERIC_TAG = "Intervalos numéricos (para resolver con un número): "

Bins = dict[str, tuple[float | None, float | None]]


def _num(value) -> float | None:
    if value is None:
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def parse_bins(raw: object, labels: list[str]) -> Bins | None:
    """Intervalos por etiqueta, en el orden de `labels`, o None si no son válidos.

    Válidos = uno por etiqueta, contiguos (el final de uno es el inicio del siguiente),
    crecientes, abiertos por abajo el primero y por arriba el último."""
    if not isinstance(raw, list) or len(raw) != len(labels) or len(labels) < 2:
        return None
    try:
        pairs = [(_num(lo), _num(hi)) for lo, hi in raw]
    except (TypeError, ValueError):
        return None
    if pairs[0][0] is not None or pairs[-1][1] is not None:
        return None
    for (lo, hi), (next_lo, _next_hi) in itertools.pairwise(pairs):
        if hi is None or next_lo is None or hi != next_lo:
            return None
        if lo is not None and not lo < hi:
            return None
    return dict(zip(labels, pairs, strict=True))


def note(unit: str, bins: Bins) -> str:
    payload = {"unit": unit, "bins": {k: list(v) for k, v in bins.items()}}
    return NUMERIC_TAG + json.dumps(payload, ensure_ascii=False)


def bins_of(forecast: Forecast) -> tuple[str, Bins] | None:
    """(unidad, intervalos) de una previsión numérica, o None si no lo es."""
    for assumption in forecast.assumptions:
        if assumption.startswith(NUMERIC_TAG):
            try:
                data = json.loads(assumption[len(NUMERIC_TAG) :])
                bins = {
                    str(k): (_num(v[0]), _num(v[1])) for k, v in (data.get("bins") or {}).items()
                }
            except (json.JSONDecodeError, TypeError, ValueError, IndexError):
                return None
            if set(bins) == set(forecast.distribution.outcomes):
                return str(data.get("unit") or ""), bins
    return None


def label_for(bins: Bins, value: float) -> str:
    """El intervalo donde cae `value` (inicio incluido, final excluido)."""
    for label, (lo, hi) in bins.items():
        if (lo is None or value >= lo) and (hi is None or value < hi):
            return label
    raise ValueError(f"{value} no cae en ningún intervalo")


def parse_number(text: str) -> float | None:
    """«1.234,5», «1,234.5», «12 %», «3e3» → número; None si no es un número."""
    cleaned = text.strip().replace(" ", "").replace(" ", "").rstrip("%€$")
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif cleaned.count(",") == 1:
        cleaned = cleaned.replace(",", ".")
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return number if math.isfinite(number) else None
