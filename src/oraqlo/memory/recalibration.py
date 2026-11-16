"""Recalibración aprendida: el Brier histórico corrige las previsiones nuevas.

Por modelo de IA se ajusta un único factor `a` (ver `forecaster.aggregate.power`)
que minimiza el Brier de sus previsiones ya resueltas. Un solo parámetro con
rango acotado es robusto con pocos datos: no sobreajusta, y con menos de
`MIN_RESOLVED` resueltas no se activa (a = 1).

El ajuste se hace sobre las probabilidades CRUDAS (antes de recalibrar) para que
la corrección no se aplique sobre sí misma en cada vuelta. No hace falta guardarlas
aparte (ni migrar la base, lo que impediría volver a la versión anterior): cada
previsión recalibrada lleva entre sus supuestos el factor usado, y como
`power(power(p, a), 1/a) = p`, lo crudo se recupera (salvo el acotado en los extremos).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from oraqlo.forecaster.aggregate import power
from oraqlo.forecaster.base import Forecast
from oraqlo.memory.calibration import CalibrationLedger, multiclass_brier

MIN_RESOLVED = 30
A_MIN, A_MAX, A_STEP = 0.5, 2.0, 0.05
LLM_PREFIX = "llm:"
# Sufijos de fuente por tipo de pregunta (la práctica usa `llm:<modelo>` sin sufijo).
SOURCE_KINDS = ("prevision", "objetivo")
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


def fit(pairs: list[tuple[dict[str, float], str]]) -> Recalibration:
    """Busca en rejilla el `a` de menor Brier medio. Empate → el más cercano a 1."""
    pairs = [(o, actual) for o, actual in pairs if len(o) >= 2]
    n = len(pairs)
    if n == 0:
        return Recalibration()

    def mean_brier(a: float) -> float:
        return sum(multiclass_brier(power(o, a), actual) for o, actual in pairs) / n

    before = mean_brier(1.0)
    if n < MIN_RESOLVED:
        return Recalibration(1.0, n, before, before)
    steps = round((A_MAX - A_MIN) / A_STEP)
    grid = [round(A_MIN + i * A_STEP, 2) for i in range(steps + 1)]
    # Penalización mínima hacia a = 1: solo se corrige si mejora de verdad.
    best = min(grid, key=lambda a: (mean_brier(a) + 1e-4 * abs(math.log(a)), abs(a - 1.0)))
    return Recalibration(best, n, before, mean_brier(best))


def raw_of(forecast: Forecast) -> dict[str, float]:
    """Probabilidades antes de recalibrar (deshace el factor anotado, si lo hay)."""
    outcomes = forecast.distribution.outcomes
    for assumption in forecast.assumptions:
        match = _NOTE_RE.match(assumption)
        if match and (a := float(match.group(1))) > 0:
            return power(outcomes, 1.0 / a)
    return dict(outcomes)


def for_model(ledger: CalibrationLedger, model: str) -> Recalibration:
    """Recalibración de un modelo con todas sus previsiones resueltas (cualquier tipo)."""
    pairs = [
        (raw_of(r.forecast), r.actual_outcome)
        for r in ledger.resolved_forecasts
        if model_of(r.forecast.source) == model
    ]
    return fit(pairs)


def by_model(ledger: CalibrationLedger) -> dict[str, Recalibration]:
    models = {m for r in ledger.resolved_forecasts if (m := model_of(r.forecast.source))}
    return {m: for_model(ledger, m) for m in sorted(models)}
