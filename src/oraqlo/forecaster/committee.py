"""Comité de modelos: varias IA, cada una con un enfoque, combinadas por su historial.

Pedir la misma pregunta al mismo modelo con el mismo prompt da casi la misma
respuesta (y Claude o gpt-5 ignoran la temperatura): agregarlas apenas aporta. Lo que
mejora una predicción combinada es que los errores sean independientes, así que:

- cada miembro es un modelo distinto (familias distintas: no fallan igual) y,
  además, mira la pregunta con un ENFOQUE distinto (tasa base, datos del caso,
  abogado del diablo, ritmo en el tiempo);
- se combinan en log-odds (`pool_log_odds`) con un peso por modelo que sale de su
  habilidad frente al azar en la Práctica, acercada a «todos iguales» mientras haya
  pocos casos (encogimiento bayesiano con `PRIOR_N` casos ficticios);
- cada miembro se registra aparte (`llm:<modelo>:miembro`) para que los pesos
  aprendan; esas filas no cuentan en el boletín de Oraqlo.

Si solo responde un modelo, todo funciona como antes.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from oraqlo.forecaster.aggregate import clamp, pool_log_odds
from oraqlo.llm.base import LLMProvider
from oraqlo.memory.calibration import CalibrationLedger, multiclass_brier

COMMITTEE_MODEL = "comite"  # «modelo» con el que se registra la predicción combinada
MEMBER_KIND = "miembro"
DEFAULT_SIZE = 4  # miembros por defecto (contando el modelo del rol)
PRIOR_N = 10  # casos ficticios «del montón» con los que empieza cada modelo
WEIGHT_SCALE = 2.0  # peso = exp(2·habilidad): +25% de habilidad ≈ 1,6× de voz

LENSES = (
    "Enfoque: VISIÓN EXTERNA. Antes de nada fija la clase de referencia (casos parecidos) "
    "y su frecuencia histórica; muévete poco de ella salvo con datos concretos de este caso.",
    "Enfoque: VISIÓN INTERNA. Céntrate en los datos concretos de este caso (estado actual, "
    "tendencias, calendario, quién decide) y en qué tendría que pasar para cada escenario.",
    "Enfoque: ABOGADO DEL DIABLO. Antes de dar cifras, busca por qué lo que parece más "
    "probable podría NO pasar: sorpresas, que todo siga igual, letra pequeña de cómo se resuelve.",
    "Enfoque: RITMO EN EL TIEMPO. Estima con qué frecuencia ocurren estas cosas (por semana o "
    "por mes) y aplícala a los días que quedan; recuerda que lo más común es que nada cambie.",
)

# Familias conocidas, en orden de preferencia para el comité por defecto.
FAMILIES = (
    "deepseek",
    "kimi",
    "glm",
    "gpt-oss",
    "qwen",
    "mistral",
    "nemotron",
    "minimax",
    "mimo",
    "gemma",
    "llama",
    "claude",
    "gpt",
    "gemini",
    "grok",
)
# Variantes pequeñas o especializadas: solo si no hay otra de esa familia.
_LESSER = re.compile(r"flash|code|coder|small|mini|nano|lite|:\d+b$|-\d+b$|20b")


def family(model: str) -> str:
    """Familia de un modelo («deepseek-v4-pro:0813» → «deepseek»)."""
    name = model.lower().split("/")[-1]
    for fam in FAMILIES:
        if name.startswith(fam):
            return fam
    return re.split(r"[-:._\d]", name, maxsplit=1)[0] or name


def pick_committee(catalog: Iterable[dict], lead_model: str, size: int = DEFAULT_SIZE) -> list[str]:
    """Modelos de la nube para acompañar al del rol: uno por familia, de las preferidas,
    el más grande y no especializado. `catalog`: [{"name", "size"}] de ollama.com."""
    taken = {family(lead_model)}
    best: dict[str, tuple] = {}
    for m in catalog:
        name = str(m.get("name") or "")
        if not name:
            continue
        fam = family(name)
        if fam in taken or fam not in FAMILIES:
            continue
        key = (bool(_LESSER.search(name.lower())), -(m.get("size") or 0), name)
        if fam not in best or key < best[fam]:
            best[fam] = key
    ordered = sorted(best, key=FAMILIES.index)
    return [best[fam][2] for fam in ordered][: max(0, size - 1)]


@dataclass(frozen=True)
class Member:
    provider: LLMProvider
    model: str
    lens: str


def build_members(
    lead: tuple[LLMProvider, str], extra: list[tuple[LLMProvider, str]], samples: int
) -> list[Member]:
    """El modelo del rol y los extra, cada uno con un enfoque; al menos `samples`
    respuestas (repartidas entre los modelos, con enfoques distintos: variedad sin
    depender de la temperatura, que Claude o gpt-5 ignoran)."""
    models = [lead, *[e for e in extra if e[1] != lead[1]]]
    n = max(len(models), samples)
    return [
        Member(models[i % len(models)][0], models[i % len(models)][1], LENSES[i % len(LENSES)])
        for i in range(n)
    ]


def member_source(model: str) -> str:
    return f"llm:{model}:{MEMBER_KIND}"


def model_skill(ledger: CalibrationLedger, model: str, decided: set[str]) -> tuple[float, int]:
    """(habilidad frente al azar, n) de las respuestas de `model` como miembro del comité,
    sin las preguntas que ya se sabían."""
    from oraqlo.memory.recalibration import market_id_of

    source = member_source(model)
    rows = [
        r
        for r in ledger.resolved_forecasts
        if r.forecast.source == source and market_id_of(r.forecast.question) not in decided
    ]
    if not rows:
        return 0.0, 0
    brier = sum(r.brier for r in rows) / len(rows)
    chance = 0.0
    for r in rows:
        labels = list(r.forecast.distribution.outcomes) or [r.actual_outcome]
        chance += multiclass_brier({k: 1 / len(labels) for k in labels}, r.actual_outcome)
    chance /= len(rows)
    return (1 - brier / chance if chance else 0.0), len(rows)


def model_weights(ledger: CalibrationLedger, models: Iterable[str]) -> dict[str, float]:
    """Peso de cada modelo: exp(2 · habilidad encogida), con la habilidad acercada a 0
    («como los demás») en proporción a lo poco que se sabe de él."""
    from oraqlo.memory.recalibration import decided_markets

    decided = decided_markets(
        [*ledger.open_forecasts, *(r.forecast for r in ledger.resolved_forecasts)]
    )
    weights = {}
    for model in models:
        skill, n = model_skill(ledger, model, decided)
        shrunk = skill * n / (n + PRIOR_N)
        weights[model] = math.exp(WEIGHT_SCALE * max(-1.0, min(1.0, shrunk)))
    return weights


def run_members[T](
    members: list[Member],
    ask: Callable[[Member], T],
    on_error: Callable[[Member, Exception], None] | None = None,
) -> list[tuple[Member, T]]:
    """Pregunta a todos a la vez; quien falla se descarta (y se avisa). Mantiene el orden."""
    if not members:
        return []
    out: list[tuple[Member, T]] = []
    if len({m.model for m in members}) == 1:
        # Un solo modelo: en orden (un Ollama local las atendería de una en una igualmente).
        for m in members:
            try:
                out.append((m, ask(m)))
            except Exception as e:
                if on_error:
                    on_error(m, e)
        return out
    with ThreadPoolExecutor(max_workers=min(8, len(members))) as pool:
        futures = [(m, pool.submit(ask, m)) for m in members]
        for m, fut in futures:
            try:
                out.append((m, fut.result()))
            except Exception as e:
                if on_error:
                    on_error(m, e)
    return out


def combine(
    draws: list[tuple[str, dict[str, float]]], weights: dict[str, float] | None = None
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """(combinada, por modelo). Primero se juntan las respuestas de cada modelo (sus
    enfoques) y luego los modelos entre sí con su peso: un modelo con tres enfoques no
    vale tres veces más que otro con uno."""
    by_model: dict[str, list[dict[str, float]]] = {}
    for model, dist in draws:
        by_model.setdefault(model, []).append(dist)
    per_model = {m: clamp(pool_log_odds(ds)) for m, ds in by_model.items()}
    models = list(per_model)
    w = [(weights or {}).get(m, 1.0) for m in models]
    pooled = clamp(pool_log_odds([per_model[m] for m in models], w))
    return pooled, per_model


def weights_note(weights: dict[str, float], models: Iterable[str]) -> str:
    models = list(models)
    total = sum(weights.get(m, 1.0) for m in models) or 1.0
    parts = ", ".join(f"{m} {weights.get(m, 1.0) / total:.0%}" for m in models)
    return f"Comité de {len(models)} modelos ({parts})."
