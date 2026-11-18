"""¿Está mejorando? Compara, sobre lo ya resuelto, distintas formas de predecir.

Solo con previsiones que cuentan (sin preguntas que ya se sabían) y siempre en
validación cruzada cuando algo se aprende del historial: cada caso se puntúa con lo
aprendido de los DEMÁS, así una mejora no es la propia respuesta copiada.

- Por modelo (o comité): tal como salieron, sin recalibrar, con la recalibración
  anterior (Brier solo, sin encoger, desde 30) y con la nueva.
- Por tipo de pregunta: la recalibración con un único factor frente a la que además
  tiene factor propio por grupo (tipo · binaria/múltiple · plazo).
- El comité: cada voz por separado (modelos, mercado, estimación por partes), todos
  con el mismo peso y con los pesos aprendidos (cada pregunta, con los pesos que
  salían sin ella), y la combinación sin las voces extra para ver lo que aportan.
- El mercado en las mismas preguntas de la Práctica, como referencia.

Cifras: n, Brier medio y «% mejor que el azar» (1 − Brier/azar).
"""

from __future__ import annotations

import math
from collections import defaultdict

from oraqlo.forecaster.aggregate import clamp, pool_log_odds, power
from oraqlo.forecaster.committee import PRIOR_N, WEIGHT_SCALE, is_extra_voice
from oraqlo.forecaster.ensemble import MEMBER_SEP
from oraqlo.memory.calibration import CalibrationLedger, ResolvedForecast, multiclass_brier
from oraqlo.memory.recalibration import (
    GRID,
    MIN_RESOLVED,
    ONE,
    choose,
    counts_for_score,
    decided_markets,
    fit,
    group_of,
    is_market,
    is_member,
    market_id_of,
    model_of,
    raw_of,
    tables,
)
from oraqlo.memory.scorecard import chance_brier

OLD_MIN = 30  # la recalibración de antes de la 0.14
MAX_CASES = 300  # por grupo, los más recientes (la validación cruzada crece con n²)
MAX_GROUP_CASES = 150  # la versión por tipo reajusta todo en cada caso: más cara


def _row(name: str, briers: list[float], chances: list[float], note: str = "") -> dict:
    n = len(briers)
    brier = sum(briers) / n if n else None
    chance = sum(chances) / n if n else None
    return {
        "name": name,
        "n": n,
        "brier": brier,
        "chance": chance,
        "skill": 1 - brier / chance if brier is not None and chance else None,
        "note": note,
    }


def _loo_recalibration(
    pairs: list[tuple[dict[str, float], str]],
) -> tuple[list[float], list[float]]:
    """Brier de cada caso con la recalibración (anterior, nueva) aprendida sin él."""
    n = len(pairs)
    briers, losses = tables(pairs)
    grid = range(len(GRID))
    tot_b = [sum(row) for row in briers]

    def old_choice(i: int) -> int:
        if n - 1 < OLD_MIN:
            return ONE
        return min(
            grid,
            key=lambda g: (
                (tot_b[g] - briers[g][i]) / (n - 1) + 1e-4 * abs(math.log(GRID[g])),
                abs(GRID[g] - 1.0),
            ),
        )

    old = [briers[old_choice(i)][i] for i in range(n)]
    new = [briers[choose(briers, losses, [j for j in range(n) if j != i])][i] for i in range(n)]
    return old, new


def _loo_groups(pairs: list[tuple[dict[str, float], str]], groups: list[str]) -> list[float]:
    """Brier de cada caso con la recalibración por tipo aprendida sin él."""
    out = []
    for i, (outcomes, actual) in enumerate(pairs):
        rest = pairs[:i] + pairs[i + 1 :]
        recal = fit(rest, groups[:i] + groups[i + 1 :])
        a = recal.factor(groups[i])
        out.append(multiclass_brier(clamp(power(outcomes, a)), actual))
    return out


def _weight(skill: float, n: int) -> float:
    shrunk = skill * n / (n + PRIOR_N)
    return math.exp(WEIGHT_SCALE * max(-1.0, min(1.0, shrunk)))


def evaluate(ledger: CalibrationLedger) -> dict:
    resolved = ledger.resolved_forecasts
    decided = decided_markets([*ledger.open_forecasts, *(r.forecast for r in resolved)])
    valid = [r for r in resolved if counts_for_score(r.forecast, decided)]

    # ── por modelo (o comité) ───────────────────────────────────────────
    groups: dict[str, list[ResolvedForecast]] = defaultdict(list)
    for r in valid:
        if (m := model_of(r.forecast.source)) is not None:
            groups[m].append(r)
    models = []
    for model, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        items = [r for r in items if len(r.forecast.distribution.outcomes) >= 2][-MAX_CASES:]
        if len(items) < 5:
            continue
        chances = [chance_brier(r) for r in items]
        pairs = [(raw_of(r.forecast), r.actual_outcome) for r in items]
        old, new = _loo_recalibration(pairs)
        recent = items[-MAX_GROUP_CASES:]
        by_group = _loo_groups(
            [(raw_of(r.forecast), r.actual_outcome) for r in recent],
            [group_of(r.forecast) for r in recent],
        )
        models.append(
            {
                "model": model,
                "rows": [
                    _row("Tal como salieron", [r.brier for r in items], chances),
                    _row(
                        "Sin recalibrar",
                        [multiclass_brier(clamp(o), y) for o, y in pairs],
                        chances,
                    ),
                    _row(
                        "Recalibración anterior",
                        old,
                        chances,
                        f"solo Brier, sin encoger, desde {OLD_MIN} resueltas",
                    ),
                    _row(
                        "Recalibración nueva",
                        new,
                        chances,
                        f"encogida, desde {MIN_RESOLVED} y solo si valida",
                    ),
                    _row(
                        "Recalibración por tipo",
                        by_group,
                        chances[-len(recent) :],
                        f"factor propio por tipo de pregunta (últimas {len(recent)})",
                    ),
                ],
            }
        )

    # ── el comité: cada modelo y la combinación ─────────────────────────
    by_id = {r.forecast.id: r for r in resolved}
    questions: dict[str, dict[str, ResolvedForecast]] = defaultdict(dict)
    for r in resolved:
        if not is_member(r.forecast.source) or MEMBER_SEP not in r.forecast.id:
            continue
        main_id, model = r.forecast.id.split(MEMBER_SEP, 1)
        main = by_id.get(main_id)
        if main is not None and counts_for_score(main.forecast, decided):
            questions[main_id][model] = r
    committee = None
    if questions:
        member_models = sorted({m for q in questions.values() for m in q})
        totals = {m: [0.0, 0.0, 0] for m in member_models}  # brier, azar, n
        for q in questions.values():
            for m, r in q.items():
                totals[m][0] += r.brier
                totals[m][1] += chance_brier(r)
                totals[m][2] += 1
        equal, learned, chances = [], [], []
        plain, plain_chances = [], []  # solo modelos, en las preguntas con voces extra
        with_extra = []
        for q in questions.values():
            r0 = next(iter(q.values()))
            labels = list(r0.forecast.distribution.outcomes)
            dists = [
                {k: r.forecast.distribution.outcomes.get(k, 0.0) for k in labels}
                for r in q.values()
            ]
            weights = []
            for m, r in q.items():
                b, c, n = totals[m]
                b, c, n = b - r.brier, c - chance_brier(r), n - 1  # sin esta pregunta
                weights.append(_weight(1 - b / c if n and c else 0.0, n))
            y = r0.actual_outcome
            equal.append(multiclass_brier(clamp(pool_log_odds(dists)), y))
            pooled = multiclass_brier(clamp(pool_log_odds(dists, weights)), y)
            learned.append(pooled)
            chances.append(chance_brier(r0))
            keep = [i for i, m in enumerate(q) if not is_extra_voice(m)]
            if keep and len(keep) < len(q):
                only = pool_log_odds([dists[i] for i in keep], [weights[i] for i in keep])
                plain.append(multiclass_brier(clamp(only), y))
                with_extra.append(pooled)
                plain_chances.append(chance_brier(r0))
        rows = []
        for m in member_models:
            b, c, n = totals[m]
            rows.append(
                {
                    "name": m,
                    "n": n,
                    "brier": b / n,
                    "chance": c / n,
                    "skill": 1 - b / c if c else None,
                    "note": "solo",
                }
            )
        rows.append(_row("Comité, mismo peso", equal, chances))
        rows.append(_row("Comité, pesos aprendidos", learned, chances, "validación cruzada"))
        if plain:
            rows.append(
                _row("Sin mercado ni partes", plain, plain_chances, "mismas preguntas, solo IA")
            )
            rows.append(_row("Con mercado y partes", with_extra, plain_chances, "mismas preguntas"))
        committee = {"questions": len(questions), "rows": rows}

    # ── el mercado, en las mismas preguntas ─────────────────────────────
    mine = {
        market_id_of(r.forecast.question): r
        for r in valid
        if market_id_of(r.forecast.question) is not None
    }
    pairs_market = [
        (mine[mid], r)
        for r in resolved
        if is_market(r.forecast.source) and (mid := market_id_of(r.forecast.question)) in mine
    ]
    market = None
    if pairs_market:
        chances = [chance_brier(o) for o, _m in pairs_market]
        market = [
            _row("Oraqlo", [o.brier for o, _m in pairs_market], chances),
            _row("Mercado (Polymarket)", [m.brier for _o, m in pairs_market], chances),
        ]

    return {"n": len(valid), "models": models, "committee": committee, "market": market}


def format_report(report: dict) -> str:
    def line(row: dict) -> str:
        skill = "—" if row["skill"] is None else f"{row['skill']:+.0%}"
        brier = "—" if row["brier"] is None else f"{row['brier']:.3f}"
        note = f"  ({row['note']})" if row.get("note") else ""
        return f"  {row['name']:<28} n={row['n']:<4} Brier {brier}  frente al azar {skill}{note}"

    out = [f"Predicciones válidas resueltas: {report['n']}"]
    if not report["models"] and not report["committee"] and not report["market"]:
        out.append("Aún no hay suficientes resueltas para comparar (hacen falta 5 de un modelo).")
    for group in report["models"]:
        out.append(f"\n{group['model']}:")
        out += [line(r) for r in group["rows"]]
    if report["committee"]:
        out.append(f"\nComité ({report['committee']['questions']} preguntas):")
        out += [line(r) for r in report["committee"]["rows"]]
    if report["market"]:
        out.append("\nFrente al mercado, en las mismas preguntas:")
        out += [line(r) for r in report["market"]]
    return "\n".join(out)
