"""OpenAICompatProvider: cualquier API compatible con OpenAI Chat Completions.

Un solo adaptador cubre OpenAI y la larga lista de servicios que exponen el
mismo protocolo (Gemini, OpenRouter, Groq, DeepSeek, Mistral, xAI, LM Studio...).
Cada preset fija la URL base y la variable de entorno de la clave.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from oraqlo.llm.base import (
    ChatMessage,
    LLMError,
    LLMProvider,
    LLMResponse,
    ProviderUnavailableError,
)


@dataclass(frozen=True)
class OpenAIPreset:
    base_url: str | None
    api_key_env: str | None
    json_schema: bool = True  # soporta response_format=json_schema


PRESETS: dict[str, OpenAIPreset] = {
    "openai": OpenAIPreset(None, "OPENAI_API_KEY"),
    "gemini": OpenAIPreset(
        "https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY"
    ),
    "openrouter": OpenAIPreset("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "groq": OpenAIPreset("https://api.groq.com/openai/v1", "GROQ_API_KEY", json_schema=False),
    "deepseek": OpenAIPreset("https://api.deepseek.com", "DEEPSEEK_API_KEY", json_schema=False),
    "mistral": OpenAIPreset("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "xai": OpenAIPreset("https://api.x.ai/v1", "XAI_API_KEY"),
    "lmstudio": OpenAIPreset("http://localhost:1234/v1", None, json_schema=False),
}


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        preset: str = "openai",
        base_url: str | None = None,
        api_key_env: str | None = None,
        timeout_s: float = 300.0,
        max_retries: int = 3,
        client: Any = None,
    ) -> None:
        if preset not in PRESETS and base_url is None:
            raise ValueError(f"Preset desconocido {preset!r}; válidos: {sorted(PRESETS)}")
        p = PRESETS.get(preset, OpenAIPreset(base_url, api_key_env))
        self.name = preset
        self.base_url = base_url or p.base_url
        self.api_key_env = api_key_env if api_key_env is not None else p.api_key_env
        self.supports_json_schema = p.json_schema
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._client = client
        self._fixed_temperature: set[str] = set()  # modelos que no aceptan temperature

    def _get_client(self) -> Any:
        if self._client is None:
            import openai

            key = os.environ.get(self.api_key_env) if self.api_key_env else "not-needed"
            if not key:
                raise ProviderUnavailableError(
                    f"Falta la clave de {self.name}: añádela en Ajustes → Proveedores."
                )
            self._client = openai.OpenAI(
                api_key=key,
                base_url=self.base_url,
                timeout=self.timeout_s,
                max_retries=self.max_retries,
            )
        return self._client

    def chat(
        self,
        messages: list[ChatMessage],
        model: str,
        temperature: float = 0.7,
        json_mode: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        import openai

        params: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if model not in self._fixed_temperature:
            params["temperature"] = temperature
        schema = kwargs.get("json_schema")
        if schema is not None and self.supports_json_schema:
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "respuesta", "schema": schema},
            }
        elif json_mode:
            params["response_format"] = {"type": "json_object"}
        if "max_tokens" in kwargs:
            params["max_tokens"] = kwargs["max_tokens"]

        try:
            response = self._create(params)
        except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
            raise ProviderUnavailableError(
                f"{self.name} rechazó la clave: revísala en Ajustes → Proveedores."
            ) from e
        except openai.NotFoundError as e:
            raise LLMError(f"Modelo no encontrado en {self.name}: {model!r}") from e
        except (openai.APIConnectionError, openai.APITimeoutError) as e:
            raise ProviderUnavailableError(f"No se pudo conectar con {self.name}: {e}") from e
        except openai.APIStatusError as e:
            raise ProviderUnavailableError(
                f"Error {e.status_code} de {self.name}: {e.message}"
            ) from e

        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "", model=response.model, raw={"id": response.id}
        )

    def _create(self, params: dict[str, Any]) -> Any:
        """Crea la respuesta adaptándose a los modelos de razonamiento (o-series,
        gpt-5…), que rechazan `temperature` y `max_tokens` con un 400."""
        import openai

        client = self._get_client()
        for _ in range(3):
            try:
                return client.chat.completions.create(**params)
            except openai.BadRequestError as e:
                message = str(e.message).lower()
                if "temperature" in message and "temperature" in params:
                    params.pop("temperature")
                    self._fixed_temperature.add(params["model"])
                elif "max_tokens" in message and "max_tokens" in params:
                    params["max_completion_tokens"] = params.pop("max_tokens")
                else:
                    raise LLMError(f"{self.name} rechazó la petición: {e.message}") from e
        return client.chat.completions.create(**params)

    def list_models(self) -> list[str]:
        import openai

        try:
            return sorted(m.id for m in self._get_client().models.list())
        except openai.OpenAIError as e:
            raise ProviderUnavailableError(f"No se pudo listar modelos de {self.name}: {e}") from e
