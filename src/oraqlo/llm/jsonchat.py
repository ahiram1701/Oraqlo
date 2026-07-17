"""chat_json: salida JSON estructurada con reintento-con-feedback.

El patrón estándar del proyecto para pedir JSON a un LLM: si la respuesta no
parsea, se le muestra el error al modelo y se le pide solo el JSON corregido
(los modelos corrigen bien su propio JSON con ese feedback).
"""

from __future__ import annotations

import json

from oraqlo.llm.base import ChatMessage, LLMProvider


class JsonChatError(ValueError):
    """El modelo no produjo un objeto JSON válido tras los reintentos."""


def extract_json_object(text: str) -> dict:
    """Extrae el primer objeto JSON del texto (tolera prosa alrededor)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"Sin objeto JSON en la respuesta: {text[:200]!r}")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("El JSON raíz no es un objeto")
    return data


def chat_json(
    provider: LLMProvider,
    model: str,
    system: str,
    user: str,
    temperature: float = 0.2,
    max_retries: int = 1,
) -> dict:
    messages = [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=user),
    ]
    last_error: Exception | None = None
    for _ in range(max_retries + 1):
        response = provider.chat(
            messages=messages, model=model, temperature=temperature, json_mode=True
        )
        try:
            return extract_json_object(response.content)
        except (ValueError, json.JSONDecodeError) as e:
            last_error = e
            messages.append(ChatMessage(role="assistant", content=response.content))
            messages.append(
                ChatMessage(
                    role="user",
                    content=f"Tu respuesta no es válida ({e}). Devuelve SOLO el JSON corregido.",
                )
            )
    raise JsonChatError(
        f"El modelo {model} no produjo JSON válido tras {max_retries + 1} intentos: {last_error}"
    )
