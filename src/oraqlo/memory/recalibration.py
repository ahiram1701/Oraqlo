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
from dataclasses import dataclass, field

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
_NOTE_RE = re.compile(r"^Recalibrado con su historial \(factor a=(\d+(?:\.\d+)?)(?:;[^)]*)?\)")
MIN_GROUP = 10  # resueltas de un grupo para que tenga factor propio
HORIZON_BUCKETS = ((31, "corto"), (183, "medio"))


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


def group_key(kind: str, n_outcomes: int, horizon_days: float) -> str:
    """Grupo de calibración: tipo · binaria/múltiple · plazo («prevision·binaria·corto»)."""
    size = "binaria" if n_outcomes <= 2 else "multiple"
    term = next((name for limit, name in HORIZON_BUCKETS if horizon_days <= limit), "largo")
    return f"{kind}·{size}·{term}"


def group_of(forecast: Forecast) -> str:
    from oraqlo.memory.scorecard import forecast_kind

    return group_key(
        forecast_kind(forecast.source, forecast.question),
        len(forecast.distribution.outcomes),
        forecast.horizon.total_seconds() / 86400,
    )


@dataclass(frozen=True)
class Recalibration:
    a: float = 1.0
    n: int = 0
    brier_before: float | None = None
    brier_after: float | None = None
    # Factor propio de los grupos donde el historial dice que se comporta distinto
    # ({grupo: (a, n)}); el resto usa `a`. Ver `fit_groups`.
    groups: dict[str, tuple[float, int]] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.n >= MIN_RESOLVED and (self.a != 1.0 or bool(self.groups))

    def factor(self, group: str | None = None) -> float:
        if self.n < MIN_RESOLVED:
            return 1.0
        if group and group in self.groups:
            return self.groups[group][0]
        return self.a

    def active_for(self, group: str | None = None) -> bool:
        return self.factor(group) != 1.0

    def apply(self, outcomes: dict[str, float], group: str | None = None) -> dict[str, float]:
        a = self.factor(group)
        return power(outcomes, a) if a != 1.0 else dict(outcomes)

    def describe(self) -> str:
        if self.n < MIN_RESOLVED:
            return f"Aprendiendo: {self.n} de {MIN_RESOLVED} previsiones resueltas."
        extra = f" Ajuste propio en {len(self.groups)} tipo(s) de pregunta." if self.groups else ""
        if self.a == 1.0:
            return f"Bien calibrado con {self.n} resueltas: no hace falta corregir.{extra}"
        tendency = "sobreconfiado: suaviza" if self.a < 1 else "subconfiado: concentra"
        return f"Con {self.n} resueltas el modelo sale {tendency} sus probabilidades.{extra}"

    def note(self, group: str | None = None) -> str:
        """Supuesto que se añade a la previsión: auditable y reversible (ver raw_of)."""
        a = self.factor(group)
        where = f"; grupo {group}" if group and group in self.groups else ""
        return f"Recalibrado con su historial (factor a={a:.2f}{where}): {self.describe()}"

    def as_dict(self) -> dict:
        return {
            "a": self.a,
            "n": self.n,
            "active": self.active,
            "min_resolved": MIN_RESOLVED,
            "brier_before": self.brier_before,
            "brier_after": self.brier_after,
            "groups": [{"group": g, "a": a, "n": n} for g, (a, n) in sorted(self.groups.items())],
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


def choose(
    briers: list[list[float]],
    losses: list[list[float]],
    keep: list[int],
    target: int = ONE,
    min_n: int = MIN_RESOLVED,
) -> int:
    """Índice del `a` que aprende la regla con los casos `keep`: el de menor pérdida
    encogida hacia `target` (a = 1, o el factor global en un grupo), y solo si mejora
    el Brier en validación cruzada; si no, `target`."""
    m = len(keep)
    if m < min_n:
        return target
    grid = range(len(GRID))
    totals = [sum(losses[g][j] for j in keep) for g in grid]
    anchor = math.log(GRID[target])

    def best(total: list[float], k: int) -> int:
        return min(
            grid,
            key=lambda g: (
                total[g] / k + SHRINK * (math.log(GRID[g]) - anchor) ** 2 / k,
                abs(GRID[g] - GRID[target]),
            ),
        )

    g = best(totals, m)
    if g == target:
        return target
    before = sum(briers[target][j] for j in keep) / m
    cv = sum(briers[best([totals[k] - losses[k][j] for k in grid], m - 1)][j] for j in keep) / m
    return g if cv < before else target


def fit(
    pairs: list[tuple[dict[str, float], str]], groups: list[str] | None = None
) -> Recalibration:
    """El `a` aprendido de `pairs` (ver `choose`), con el Brier antes y después.

    Con `groups` (uno por par), cada grupo con al menos `MIN_GROUP` casos puede tener
    su propio factor, encogido hacia el global y solo si mejora en validación cruzada."""
    keep_idx = [i for i, (o, _a) in enumerate(pairs) if len(o) >= 2]
    pairs = [pairs[i] for i in keep_idx]
    labels = [groups[i] for i in keep_idx] if groups is not None else None
    n = len(pairs)
    if n == 0:
        return Recalibration()
    briers, losses = tables(pairs)
    g = choose(briers, losses, list(range(n)))
    own: dict[str, tuple[float, int]] = {}
    chosen = [g] * n
    if labels is not None and n >= MIN_RESOLVED:
        for name in sorted(set(labels)):
            members = [i for i, lab in enumerate(labels) if lab == name]
            if len(members) < MIN_GROUP:
                continue
            gg = choose(briers, losses, members, target=g, min_n=MIN_GROUP)
            if gg != g:
                own[name] = (GRID[gg], len(members))
                for i in members:
                    chosen[i] = gg
    before = sum(briers[ONE]) / n
    after = sum(briers[chosen[i]][i] for i in range(n)) / n
    return Recalibration(GRID[g], n, before, after, own)


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
    """Recalibración de un modelo (o del comité) con sus previsiones válidas resueltas,
    con factor propio en los tipos de pregunta donde se comporta distinto."""
    rows = [r for r in valid_resolved(ledger) if model_of(r.forecast.source) == model]
    pairs = [(raw_of(r.forecast), r.actual_outcome) for r in rows]
    return fit(pairs, [group_of(r.forecast) for r in rows])


def by_model(ledger: CalibrationLedger) -> dict[str, Recalibration]:
    models = {m for r in valid_resolved(ledger) if (m := model_of(r.forecast.source))}
    return {m: for_model(ledger, m) for m in sorted(models)}
