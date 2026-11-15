"""Abstracción de proveedor LLM: una interfaz, muchos backends.

Implementaciones: Ollama (local + cloud), Anthropic (Claude) y cualquier API
compatible con OpenAI (OpenAI, Gemini, OpenRouter, Groq, DeepSeek, Mistral,
LM Studio...). El resto del sistema solo ve `LLMProvider.chat()`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMResponse:
    content: str
    model: str
    raw: dict = field(default_factory=dict)


class LLMError(RuntimeError):
    """Error base de la capa LLM."""


class ProviderUnavailableError(LLMError):
    """El backend no está accesible: apagado, sin red, sin API key o credencial rechazada.

    Siempre con mensaje accionable (qué falta y cómo arreglarlo), nunca un stack trace.
    """


class LLMProvider(ABC):
    """Contrato mínimo: chat de una tanda (quien llama gestiona el historial).

    `supports_json_schema`: si True, `chat(json_schema=...)` usa la salida
    estructurada nativa del backend (el modelo queda restringido al esquema).
    Si False, quien llama no debe pasar `json_schema` y valida a posteriori.
    """

    name: str = "llm"
    supports_json_schema: bool = False

    @abstractmethod
    def chat(
        self,
        messages: list[ChatMessage],
        model: str,
        temperature: float = 0.7,
        json_mode: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        """Envía la conversación y devuelve la respuesta del modelo.

        `json_mode=True` pide salida JSON. Kwargs opcionales reconocidos:
        `json_schema` (dict JSON Schema; solo si `supports_json_schema`) y
        `max_tokens`.
        """
        ...

    def list_models(self) -> list[str]:
        """Modelos disponibles en el backend (vacío si el backend no lo expone)."""
        return []

    def is_available(self) -> bool:
        """Comprobación barata de disponibilidad (para avisar en vez de fallar)."""
        try:
            self.list_models()
            return True
        except ProviderUnavailableError:
            return False
