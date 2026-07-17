"""Drafter: convierte una pregunta en lenguaje natural en un caso completo.

Para el usuario que solo quiere escribir su pregunta: el LLM propone las
acciones candidatas, sus resultados con probabilidades y las utilidades, y el
resultado se valida con parse_case (con reintento-con-feedback, como el resto
de salidas estructuradas del sistema). El caso generado es un borrador honesto:
editable en el formulario antes o después de ejecutarlo.
"""

from __future__ import annotations

import json
from datetime import date

from oraqlo.casefile import CaseError, CaseSpec, parse_case
from oraqlo.llm.base import ChatMessage, LLMProvider
from oraqlo.llm.jsonchat import extract_json_object

_SYSTEM_PROMPT = (
    "Eres el analista de Oraqlo. Dada una pregunta estratégica, propone el caso de "
    "decisión completo. Responde EXCLUSIVAMENTE con JSON de esta forma exacta:\n"
    '{"actions": {"<id-kebab>": {"description": "<qué se haría>", '
    '"outcomes": {"<resultado>": <prob>, ...}}, ...}, '
    '"utilities": {"<resultado>": <valor>, ...}}\n'
    "Reglas: entre 2 y 4 acciones mutuamente distintas (incluye siempre alguna forma "
    "de 'no hacer nada' o esperar); por acción, entre 2 y 4 resultados cuyas "
    "probabilidades sumen 1.0; cada resultado que uses debe tener utilidad en "
    "utilities, en la escala -1.0 (ruina) a 1.0 (mejor resultado plausible), y si un "
    "resultado aparece en varias acciones su utilidad es una sola; sé concreto y usa "
    "el idioma de la pregunta."
)


class DraftError(ValueError):
    """El LLM no produjo un caso válido tras los reintentos."""


def draft_case(
    provider: LLMProvider,
    model: str,
    question: str,
    horizon_days: int,
    temperature: float = 0.4,
    max_retries: int = 2,
    context: str | None = None,
) -> CaseSpec:
    """Genera y valida un CaseSpec a partir de la pregunta. Lanza DraftError si falla.

    `context` es información de apoyo (p. ej. el brief del investigador web) que
    entra como datos del prompt, nunca como instrucciones.
    """
    if not question.strip():
        raise DraftError("La pregunta está vacía.")

    user_prompt = (
        f"Fecha de hoy: {date.today().isoformat()}\n"
        f"Pregunta estratégica: {question}\nHorizonte: {horizon_days} días"
    )
    if context:
        user_prompt += f"\n\n{context}"
    messages = [
        ChatMessage(role="system", content=_SYSTEM_PROMPT),
        ChatMessage(role="user", content=user_prompt),
    ]
    last_error: Exception | None = None
    for _ in range(max_retries + 1):
        response = provider.chat(
            messages=messages, model=model, temperature=temperature, json_mode=True
        )
        try:
            data = extract_json_object(response.content)
            case = {
                "question": question,
                "horizon_days": horizon_days,
                "actions": data.get("actions", {}),
                "utilities": data.get("utilities", {}),
                # Riesgo por defecto conservador; el usuario lo ajusta en el formulario.
                "risk": {"risk_aversion": 0.3, "ruin_threshold": -0.9,
                         "max_ruin_probability": 0.25},
            }
            return parse_case(case)
        except (CaseError, json.JSONDecodeError, TypeError, ValueError) as e:
            last_error = e
            messages.append(ChatMessage(role="assistant", content=response.content))
            messages.append(
                ChatMessage(
                    role="user",
                    content=f"Tu caso no es válido ({e}). Devuelve SOLO el JSON corregido.",
                )
            )
    raise DraftError(
        f"El modelo {model} no produjo un caso válido tras {max_retries + 1} intentos: {last_error}"
    )
