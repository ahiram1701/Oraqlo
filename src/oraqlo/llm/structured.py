"""Salida estructurada del LLM: un único camino para todo el proyecto.

1. Si el proveedor soporta JSON Schema nativo y hay esquema Pydantic, el modelo
   queda restringido al esquema en origen.
2. Siempre se valida a posteriori (extracción robusta + Pydantic).
3. Si no valida, se le muestra el error al modelo y se pide solo el JSON
   corregido (los modelos corrigen bien su propio JSON con ese feedback).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from oraqlo.llm.base import ChatMessage, LLMProvider

_DECODER = json.JSONDecoder()


class JsonChatError(ValueError):
    """El modelo no produjo un objeto JSON válido tras los reintentos."""


def extract_json_object(text: str) -> dict:
    """Extrae el primer objeto JSON *válido* del texto.

    Tolera prosa alrededor, bloques ```json``` y llaves sueltas en el texto
    (p. ej. "usa {x}" antes del JSON): prueba a decodificar desde cada '{'
    en orden y se queda con el primer objeto que parsea entero.
    """
    idx = text.find("{")
    last_error: Exception | None = None
    while idx != -1:
        try:
            data, _ = _DECODER.raw_decode(text, idx)
        except json.JSONDecodeError as e:
            last_error = e
        else:
            if isinstance(data, dict):
                return data
        idx = text.find("{", idx + 1)
    if last_error is not None:
        raise ValueError(f"JSON inválido en la respuesta: {last_error}")
    raise ValueError(f"Sin objeto JSON en la respuesta: {text[:200]!r}")


def _converse(
    provider: LLMProvider,
    model: str,
    messages: list[ChatMessage],
    temperature: float,
    max_retries: int,
    parse: Any,
    json_schema: dict | None,
) -> Any:
    last_error: Exception | None = None
    extra: dict[str, Any] = {}
    if json_schema is not None and provider.supports_json_schema:
        extra["json_schema"] = json_schema
    for _ in range(max_retries + 1):
        response = provider.chat(
            messages=messages, model=model, temperature=temperature, json_mode=True, **extra
        )
        try:
            return parse(response.content)
        except (ValueError, ValidationError) as e:
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


def chat_json(
    provider: LLMProvider,
    model: str,
    system: str,
    user: str,
    temperature: float = 0.2,
    max_retries: int = 1,
) -> dict:
    """Pide un objeto JSON libre (validación de forma a cargo de quien llama)."""
    messages = [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]
    return _converse(provider, model, messages, temperature, max_retries, extract_json_object, None)


def chat_model[M: BaseModel](
    provider: LLMProvider,
    model: str,
    system: str,
    user: str,
    schema: type[M],
    temperature: float = 0.2,
    max_retries: int = 1,
) -> M:
    """Pide un objeto que valide contra el modelo Pydantic `schema`."""
    messages = [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]

    def parse(text: str) -> M:
        return schema.model_validate(extract_json_object(text))

    return _converse(
        provider, model, messages, temperature, max_retries, parse, schema.model_json_schema()
    )
