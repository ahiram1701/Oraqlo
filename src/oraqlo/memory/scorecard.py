"""Cómo de bien acierta Oraqlo, en cifras que se entienden.

Sobre las previsiones resueltas (filtradas por tipo, modelo o periodo) calcula:

- Brier medio y el del **azar**: repartir la probabilidad por igual entre los
  escenarios de cada pregunta. La **habilidad** = 1 − Brier/azar dice cuánto
  mejor que eso lo hace (0 = igual que el azar; 1 = perfecto; negativo = peor).
- Acierto del escenario más probable y la confianza media que tenía: si dice
  80% de media y acierta el 60%, se está pasando de confiado.
- Fiabilidad por previsión (confianza en lo más probable frente a cuántas veces
  acertó). Un punto por previsión: contar cada etiqueta inflaba los números y
  mezclaba escenarios que no son independientes.
- Tendencia mensual, desglose por tipo y por plazo, y los peores fallos.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

from oraqlo.forecaster.base import Forecast
from oraqlo.memory.calibration import ResolvedForecast, multiclass_brier
from oraqlo.memory.recalibration import (
    SOURCE_KINDS,
    counts_for_score,
    decided_markets,
    is_market,
    is_practice,
    market_id_of,
    model_of,
)

KINDS = ("prevision", "objetivo", "panel", "practica")
HORIZONS = (
    ("corto", "Hasta 1 mes", 31),
    ("medio", "De 1 a 6 meses", 183),
    ("largo", "Más de 6 meses", None),
)
LOG_EPS = 1e-3  # un 0% que ocurre no es infinitamente malo en el resumen


def forecast_kind(source: str, question: str = "") -> str:
    """Qué produjo la previsión (mismo criterio que la interfaz). Una `llm:<modelo>`
    sin tipo y sin etiqueta de mercado es una previsión de antes de la 0.7."""
    if is_practice(source, question):
        return "practica"
    for kind in SOURCE_KINDS:
        if source.endswith(f":{kind}"):
            return kind
    return "prevision"


def chance_brier(r: ResolvedForecast) -> float:
    """Brier de repartir por igual entre los escenarios de esa pregunta."""
    labels = list(r.forecast.distribution.outcomes) or [r.actual_outcome]
    uniform = {label: 1.0 / len(labels) for label in labels}
    return multiclass_brier(uniform, r.actual_outcome)


def _top(r: ResolvedForecast) -> tuple[str, float]:
    outcomes = r.forecast.distribution.outcomes
    label = max(outcomes, key=lambda k: outcomes[k]) if outcomes else ""
    return label, outcomes.get(label, 0.0)


def split_market(
    resolved: Iterable[ResolvedForecast], open_forecasts: Iterable[Forecast] = ()
) -> tuple[list[ResolvedForecast], list[ResolvedForecast]]:
    """(las de Oraqlo, las del mercado), sin las preguntas que ya se sabían.

    El mercado es el rival de la Práctica, no parte del boletín de Oraqlo; antes se
    mezclaba y la Calibración salía mucho mejor de lo que era."""
    resolved = list(resolved)
    decided = decided_markets([*open_forecasts, *(r.forecast for r in resolved)])
    mine = [r for r in resolved if counts_for_score(r.forecast, decided)]
    market = [
        r
        for r in resolved
        if is_market(r.forecast.source) and market_id_of(r.forecast.question) not in decided
    ]
    return mine, market


def _stats(items: list[ResolvedForecast]) -> dict:
    n = len(items)
    if not n:
        return {"n": 0, "brier": None, "chance": None, "skill": None}
    brier = sum(r.brier for r in items) / n
    chance = sum(chance_brier(r) for r in items) / n
    return {
        "n": n,
        "brier": brier,
        "chance": chance,
        "skill": 1 - brier / chance if chance else None,
    }


def _group(items: Iterable[ResolvedForecast], key: Callable[[ResolvedForecast], str]) -> dict:
    groups: dict[str, list[ResolvedForecast]] = {}
    for r in items:
        groups.setdefault(key(r), []).append(r)
    return groups


def _horizon(r: ResolvedForecast) -> str:
    days = r.forecast.horizon.total_seconds() / 86400
    for key, _label, limit in HORIZONS:
        if limit is None or days <= limit:
            return key
    return HORIZONS[-1][0]


def select(
    resolved: Iterable[ResolvedForecast],
    kind: str | None = None,
    model: str | None = None,
    days: int | None = None,
    now: datetime | None = None,
) -> list[ResolvedForecast]:
    since = (now or datetime.now(UTC)) - timedelta(days=days) if days else None
    out = []
    for r in resolved:
        if kind and forecast_kind(r.forecast.source, r.forecast.question) != kind:
            continue
        if model and model_of(r.forecast.source) != model:
            continue
        if since and r.resolved_at < since:
            continue
        out.append(r)
    return out


def scorecard(
    items: list[ResolvedForecast],
    bins: int = 10,
    worst: int = 5,
    reference: list[ResolvedForecast] | None = None,
) -> dict:
    """`reference`: previsiones del mercado; solo salen como fila de comparación en
    «sources», nunca en el resto de cifras."""
    stats = _stats(items)
    tops = [_top(r) for r in items]
    hits = [label == r.actual_outcome for r, (label, _p) in zip(items, tops, strict=True)]
    n = len(items)
    confidence = sum(p for _l, p in tops) / n if n else None
    hit_rate = sum(hits) / n if n else None
    log_loss = (
        sum(
            -math.log(max(r.forecast.distribution.outcomes.get(r.actual_outcome, 0.0), LOG_EPS))
            for r in items
        )
        / n
        if n
        else None
    )

    buckets = [[0, 0, 0.0] for _ in range(bins)]  # [n, aciertos, suma de confianza]
    for (_label, p), hit in zip(tops, hits, strict=True):
        i = min(int(p * bins), bins - 1)
        buckets[i][0] += 1
        buckets[i][1] += int(hit)
        buckets[i][2] += p

    trend = []
    for month, group in sorted(_group(items, lambda r: r.resolved_at.strftime("%Y-%m")).items()):
        trend.append({"month": month, **_stats(group)})

    by_kind = [
        {"kind": k, **_stats(g)}
        for k, g in sorted(
            _group(items, lambda r: forecast_kind(r.forecast.source, r.forecast.question)).items(),
            key=lambda kv: KINDS.index(kv[0]),
        )
    ]
    horizon_groups = _group(items, _horizon)
    by_horizon = [
        {"horizon": key, "label": label, **_stats(horizon_groups[key])}
        for key, label, _limit in HORIZONS
        if key in horizon_groups
    ]
    by_source = [
        {"source": s, **_stats(g)} for s, g in _group(items, lambda r: r.forecast.source).items()
    ]
    by_source.sort(key=lambda x: x["brier"])
    if reference:
        by_source.append(
            {"source": reference[0].forecast.source, **_stats(reference), "reference": True}
        )

    # Solo fallos de verdad: acertar con un 52% también tiene Brier alto, pero no es
    # «donde más falló».
    misses = [r for r, hit in zip(items, hits, strict=True) if not hit]
    worst_items = sorted(misses, key=lambda r: r.brier, reverse=True)[:worst]
    return {
        "summary": {
            **stats,
            "log_loss": log_loss,
            "hit_rate": hit_rate,
            "confidence": confidence,
            # > 0: se pasa de confiado; < 0: se queda corto.
            "overconfidence": (confidence - hit_rate)
            if confidence is not None and hit_rate is not None
            else None,
        },
        "reliability": [
            {
                "bin": i,
                "p_mid": (i + 0.5) / bins,
                "confidence": (total / k) if k else None,
                "n": k,
                "observed": (h / k) if k else None,
            }
            for i, (k, h, total) in enumerate(buckets)
        ],
        "trend": trend,
        "by_kind": by_kind,
        "by_horizon": by_horizon,
        "sources": by_source,
        "worst": [
            {
                "id": r.forecast.id,
                "question": r.forecast.question,
                "source": r.forecast.source,
                "predicted": label,
                "predicted_p": p,
                "actual": r.actual_outcome,
                "actual_p": r.forecast.distribution.outcomes.get(r.actual_outcome, 0.0),
                "brier": r.brier,
                "resolved_at": r.resolved_at.isoformat(),
            }
            for r in worst_items
            for label, p in [_top(r)]
        ],
    }
