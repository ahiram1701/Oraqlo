"""Recalibración aprendida: el historial resuelto corrige las previsiones nuevas.

Por modelo de IA (o para el comité) se ajusta un único factor `a` (ver
`forecaster.aggregate.power`): a < 1 suaviza, a > 1 concentra (en un comité hace de
«extremizado» aprendido). Para no aprender ruido:

- solo cuentan sus previsiones válidas (`counts_for_score`: sin las preguntas que ya
  se sabían, que enseñarían a confiar de más);
- la pérdida es Brier + log-pérdida/4: castiga más los fallos con mucha seguridad;
- el factor se acerca a 1 con una penalización que pesa más cuantos menos casos hay;
- desde `MIN_RESOLVED` resueltas, y solo si mejora en validación cruzada (cada caso se
  predice con el `a` aprendido de los demás). Si no mejora, a = 1.

El ajuste se hace sobre las probabilidades CRUDAS (antes de recalibrar) para que
la corrección no se aplique sobre sí misma en cada vuelta. No hace falta guardarlas
aparte (ni migrar la base, lo que impediría volver a la versión anterior): cada
previsión recalibrada lleva entre sus supuestos el factor usado, y como
`power(power(p, a), 1/a) = p`, lo crudo se recupera (salvo el acotado en los extremos).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

from oraqlo.forecaster.aggregate import power
from oraqlo.forecaster.base import Forecast
from oraqlo.memory.calibration import CalibrationLedger, multiclass_brier

MIN_RESOLVED = 15
A_MIN, A_MAX, A_STEP = 0.5, 2.0, 0.05
SHRINK = 1.0  # penalización (log a)² · SHRINK / n hacia a = 1
LOG_WEIGHT = 0.25  # peso de la log-pérdida en la pérdida del ajuste
LLM_PREFIX = "llm:"
# Sufijos de fuente por tipo de pregunta (la práctica usa `llm:<modelo>` sin sufijo).
# «miembro»: lo que dijo cada modelo del comité por separado (no cuenta en el boletín).
SOURCE_KINDS = ("prevision", "objetivo", "panel", "miembro")
MEMBER_SUFFIX = ":miembro"
_NOTE_RE = re.compile(r"^Recalibrado con su historial \(factor a=(\d+(?:\.\d+)?)\)")


def llm_source(model: str, kind: str | None = None) -> str:
    """Fuente con la que se registran las previsiones de un modelo."""
    return f"{LLM_PREFIX}{model}" + (f":{kind}" if kind else "")


def model_of(source: str) -> str | None:
    """Modelo de una fuente `llm:...` (los nombres de modelo pueden llevar ':')."""
    if not source.startswith(LLM_PREFIX):
        return None
    rest = source[len(LLM_PREFIX) :]
    for kind in SOURCE_KINDS:
        if rest.endswith(f":{kind}"):
            return rest[: -len(kind) - 1]
    return rest


MARKET_TAG_PREFIX = "[polymarket:"  # ver autopractice.market_tag
MARKET_SOURCE = "polymarket:mercado"  # la previsión del mercado, el rival de la Práctica
# Si el mercado ya daba ≥98,5% al predecir, el resultado ya se sabía (p. ej. un
# partido ya jugado y aún abierto en Polymarket): esa pregunta no mide nada.
DECIDED = 0.985
_MARKET_ID_RE = re.compile(r"^\[polymarket:(\d+)\]")


def market_id_of(question: str) -> str | None:
    """Id del mercado de Polymarket de una pregunta de la Práctica."""
    match = _MARKET_ID_RE.match(question)
    return match.group(1) if match else None


def is_market(source: str) -> bool:
    """La previsión es del mercado (el rival), no de Oraqlo."""
    return source.startswith("polymarket:")


def is_member(source: str) -> bool:
    """Lo que dijo un modelo del comité por separado (la previsión es la combinada)."""
    return source.endswith(MEMBER_SUFFIX)


def decided_markets(forecasts: Iterable[Forecast]) -> set[str]:
    """Mercados cuyo resultado ya se sabía cuando Oraqlo predijo."""
    return {
        mid
        for f in forecasts
        if is_market(f.source)
        and (mid := market_id_of(f.question))
        and max(f.distribution.outcomes.values(), default=0.0) >= DECIDED
    }


def counts_for_score(f: Forecast, decided: set[str]) -> bool:
    """Entra en el boletín de Oraqlo: es suya y no era una pregunta ya sabida.

    El mismo criterio que los duelos de la Práctica, para que las dos pantallas
    cuenten las mismas preguntas."""
    if is_market(f.source) or is_member(f.source):
        return False
    mid = market_id_of(f.question)
    return not (mid and mid in decided)


def is_practice(source: str, question: str = "") -> bool:
    """De la Práctica: el mercado o la previsión de Oraqlo sobre un mercado.

    Se reconoce por la etiqueta del mercado en la pregunta: la fuente sola no
    basta, porque las previsiones de antes de la 0.7 eran todas `llm:<modelo>`."""
    return is_market(source) or question.startswith(MARKET_TAG_PREFIX)


def user_resolvable(source: str, question: str = "") -> bool:
    """La resuelve el usuario (no la Práctica, que se resuelve sola con el mercado)."""
    return not is_practice(source, question)


@dataclass(frozen=True)
class Recalibration:
    a: float = 1.0
    n: int = 0
    brier_before: float | None = None
    brier_after: float | None = None

    @property
    def active(self) -> bool:
        return self.n >= MIN_RESOLVED and self.a != 1.0

    def apply(self, outcomes: dict[str, float]) -> dict[str, float]:
        return power(outcomes, self.a) if self.active else dict(outcomes)

    def describe(self) -> str:
        if self.n < MIN_RESOLVED:
            return f"Aprendiendo: {self.n} de {MIN_RESOLVED} previsiones resueltas."
        if self.a == 1.0:
            return f"Bien calibrado con {self.n} resueltas: no hace falta corregir."
        tendency = "sobreconfiado: suaviza" if self.a < 1 else "subconfiado: concentra"
        return f"Con {self.n} resueltas el modelo sale {tendency} sus probabilidades."

    def note(self) -> str:
        """Supuesto que se añade a la previsión: auditable y reversible (ver raw_of)."""
        return f"Recalibrado con su historial (factor a={self.a:.2f}): {self.describe()}"

    def as_dict(self) -> dict:
        return {
            "a": self.a,
            "n": self.n,
            "active": self.active,
            "min_resolved": MIN_RESOLVED,
            "brier_before": self.brier_before,
            "brier_after": self.brier_after,
            "summary": self.describe(),
        }


def _loss(outcomes: dict[str, float], actual: str) -> float:
    p = outcomes.get(actual, 0.0)
    return multiclass_brier(outcomes, actual) - LOG_WEIGHT * math.log(max(p, 1e-3))


GRID = [round(A_MIN + i * A_STEP, 2) for i in range(round((A_MAX - A_MIN) / A_STEP) + 1)]
ONE = GRID.index(1.0)


def tables(
    pairs: list[tuple[dict[str, float], str]],
) -> tuple[list[list[float]], list[list[float]]]:
    """(Brier, pérdida) de cada caso para cada `a` de la rejilla: lo demás son sumas."""
    briers = [[multiclass_brier(power(o, a), y) for o, y in pairs] for a in GRID]
    losses = [[_loss(power(o, a), y) for o, y in pairs] for a in GRID]
    return briers, losses


def choose(briers: list[list[float]], losses: list[list[float]], keep: list[int]) -> int:
    """Índice del `a` que aprende la regla con los casos `keep`: el de menor pérdida
    encogida hacia 1, y solo si mejora el Brier en validación cruzada; si no, a = 1."""
    m = len(keep)
    if m < MIN_RESOLVED:
        return ONE
    grid = range(len(GRID))
    totals = [sum(losses[g][j] for j in keep) for g in grid]

    def best(total: list[float], k: int) -> int:
        return min(
            grid,
            key=lambda g: (
                total[g] / k + SHRINK * math.log(GRID[g]) ** 2 / k,
                abs(GRID[g] - 1.0),
            ),
        )

    g = best(totals, m)
    if g == ONE:
        return ONE
    before = sum(briers[ONE][j] for j in keep) / m
    cv = sum(briers[best([totals[k] - losses[k][j] for k in grid], m - 1)][j] for j in keep) / m
    return g if cv < before else ONE


def fit(pairs: list[tuple[dict[str, float], str]]) -> Recalibration:
    """El `a` aprendido de `pairs` (ver `choose`), con el Brier antes y después."""
    pairs = [(o, actual) for o, actual in pairs if len(o) >= 2]
    n = len(pairs)
    if n == 0:
        return Recalibration()
    briers, losses = tables(pairs)
    g = choose(briers, losses, list(range(n)))
    before = sum(briers[ONE]) / n
    return Recalibration(GRID[g], n, before, sum(briers[g]) / n)


def raw_of(forecast: Forecast) -> dict[str, float]:
    """Probabilidades antes de recalibrar (deshace el factor anotado, si lo hay)."""
    outcomes = forecast.distribution.outcomes
    for assumption in forecast.assumptions:
        match = _NOTE_RE.match(assumption)
        if match and (a := float(match.group(1))) > 0:
            return power(outcomes, 1.0 / a)
    return dict(outcomes)


def valid_resolved(ledger: CalibrationLedger) -> list:
    """Resueltas que cuentan (de Oraqlo y sobre preguntas que no se sabían ya)."""
    decided = decided_markets(
        [*ledger.open_forecasts, *(r.forecast for r in ledger.resolved_forecasts)]
    )
    return [r for r in ledger.resolved_forecasts if counts_for_score(r.forecast, decided)]


def for_model(ledger: CalibrationLedger, model: str) -> Recalibration:
    """Recalibración de un modelo (o del comité) con sus previsiones válidas resueltas."""
    pairs = [
        (raw_of(r.forecast), r.actual_outcome)
        for r in valid_resolved(ledger)
        if model_of(r.forecast.source) == model
    ]
    return fit(pairs)


def by_model(ledger: CalibrationLedger) -> dict[str, Recalibration]:
    models = {m for r in valid_resolved(ledger) if (m := model_of(r.forecast.source))}
    return {m: for_model(ledger, m) for m in sorted(models)}
