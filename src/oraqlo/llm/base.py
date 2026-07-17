"""Abstracción de proveedor LLM. Ollama es la única implementación prevista;
la interfaz existe para poder testear con dobles y para aislar el protocolo."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMResponse:
    content: str
    model: str
    raw: dict = field(default_factory=dict)


class LLMProvider(ABC):
    """Contrato mínimo: chat de una tanda (el panel gestiona el historial)."""

    @abstractmethod
    def chat(
        self,
        messages: list[ChatMessage],
        model: str,
        temperature: float = 0.7,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Envía la conversación y devuelve la respuesta del modelo.

        `json_mode=True` pide al backend salida JSON estructurada (para roles
        que emiten probabilidades u objetos, como el Oráculo).
        """
        ...
