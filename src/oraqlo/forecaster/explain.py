"""La respuesta en palabras llanas, con las cifras ya finales.

El Oráculo devuelve escenarios con probabilidades, pero eso no siempre responde a
la pregunta tal como se hizo: a «¿cuándo habrá una crisis?» no responde bien un
titular «No hay crisis antes de 2036 · 61%». Aquí se pide al modelo, ya con las
probabilidades agregadas y recalibradas (las que verá el usuario), una respuesta
directa y el porqué, sin jerga.

Se guarda como supuestos «Respuesta: …» y «En pocas palabras: …» (como ya se hace
con «Tasa base:» y «Razonamiento:»): no hace falta tocar el esquema de la base.
Si el modelo falla, la previsión sigue igual, sin la respuesta.
"""

from __future__ import annotations

import logging

from oraqlo.forecaster.base import Forecast
from oraqlo.llm.base import LLMProvider
from oraqlo.llm.structured import chat_json

log = logging.getLogger(__name__)

ANSWER_PREFIX = "Respuesta: "
WHY_PREFIX = "En pocas palabras: "

_SYSTEM = (
    "Explicas previsiones a alguien sin formación técnica. Recibes una pregunta y "
    "los escenarios con sus probabilidades YA CALCULADAS: no las cambies ni inventes "
    "otras. Responde SOLO con JSON: "
    '{"answer": "<1-2 frases>", "why": "<1-2 frases>"}\n'
    "answer: responde DIRECTAMENTE a la pregunta tal como está hecha, con las cifras "
    "redondeadas a porcentajes enteros. Si pregunta CUÁNDO, di la probabilidad de que "
    "ocurra dentro del plazo (suma los escenarios en que ocurre) y, si ocurre, cuándo "
    "es más probable. Si es de sí o no, empieza por «Probablemente sí», «Probablemente "
    "no» o «Es incierto». Si pide un número, da el más probable y el rango.\n"
    "why: el motivo principal en lenguaje cotidiano. Sin jerga ni siglas sin explicar, "
    "sin nombres de autores o estudios, sin palabras como «tasa base», «régimen» o "
    "«per cápita».\n"
    "Escribe en el idioma de la pregunta. Nunca des certezas."
)


def _clean(text: object) -> str:
    return " ".join(str(text or "").split())


def explain(provider: LLMProvider, model: str, question: str, forecast: Forecast) -> Forecast:
    """La misma previsión con «Respuesta:» y «En pocas palabras:» al principio de sus
    supuestos (si el modelo responde bien; si no, tal cual)."""
    outcomes = sorted(forecast.distribution.outcomes.items(), key=lambda kv: -kv[1])
    if not outcomes:
        return forecast  # previsiones numéricas: se quedan como están
    days = round(forecast.horizon.total_seconds() / 86400)
    lines = "\n".join(f"- {label}: {p:.0%}" for label, p in outcomes)
    reasoning = [a for a in forecast.assumptions if a.startswith(("Tasa base:", "Razonamiento:"))]
    user = f"Pregunta: {question}\nPlazo: {days} días\nEscenarios y probabilidades:\n{lines}" + (
        "\nCómo se llegó a ellas:\n" + "\n".join(reasoning) if reasoning else ""
    )
    try:
        data = chat_json(provider, model, _SYSTEM, user, temperature=0.2)
    except Exception as e:  # una explicación que falla no tumba la previsión
        log.warning("No se pudo explicar la previsión: %s", e)
        return forecast
    answer, why = _clean(data.get("answer")), _clean(data.get("why"))
    if not answer:
        return forecast
    extra = [ANSWER_PREFIX + answer] + ([WHY_PREFIX + why] if why else [])
    kept = [a for a in forecast.assumptions if not a.startswith((ANSWER_PREFIX, WHY_PREFIX))]
    forecast.assumptions = extra + kept
    return forecast
