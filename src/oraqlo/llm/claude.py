"""AnthropicProvider: Claude vía el SDK oficial `anthropic`.

Notas de la API actual (ver skill claude-api):
- Los modelos Claude actuales no aceptan `temperature`: la profundidad se
  controla con `effort` (low | medium | high | xhigh | max). Oraqlo mapea la
  temperatura del rol a nada y usa el `effort` configurado por rol.
- Salida estructurada nativa: `output_config.format = {type: json_schema, ...}`.
- Rechazos: con `fallbacks: "default"` (beta) la API reintenta en otro modelo
  dentro de la misma llamada; si aun así hay rechazo, `stop_reason == "refusal"`.
- El SDK ya reintenta 408/409/429/5xx y errores de conexión (`max_retries`).
"""

from __future__ import annotations

import os
from typing import Any

from oraqlo.llm.base import (
    ChatMessage,
    LLMError,
    LLMProvider,
    LLMResponse,
    ProviderUnavailableError,
)

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def strict_schema(schema: dict) -> dict:
    """Copia del JSON Schema con `additionalProperties: false` en cada objeto
    (requisito de la salida estructurada estricta)."""

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items()}
            if out.get("type") == "object":
                out.setdefault("additionalProperties", False)
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    supports_json_schema = True

    def __init__(
        self,
        api_key_env: str = "ANTHROPIC_API_KEY",
        effort: str | None = None,
        max_tokens: int = 16000,
        fallbacks: bool = True,
        timeout_s: float = 600.0,
        max_retries: int = 3,
        client: Any = None,
    ) -> None:
        if effort is not None and effort not in EFFORTS:
            raise ValueError(f"effort inválido: {effort!r} (válidos: {EFFORTS})")
        self.api_key_env = api_key_env
        self.effort = effort
        self.max_tokens = max_tokens
        self.fallbacks = fallbacks
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic

            key = os.environ.get(self.api_key_env)
            # Sin clave en env el SDK aún puede resolver credenciales (perfil `ant auth login`).
            self._client = anthropic.Anthropic(
                api_key=key or None, timeout=self.timeout_s, max_retries=self.max_retries
            )
        return self._client

    def _request(
        self, messages: list[ChatMessage], model: str, json_schema: dict | None, max_tokens: int
    ) -> dict[str, Any]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        convo = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        params: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": convo}
        if system:
            params["system"] = system
        output_config: dict[str, Any] = {}
        if self.effort:
            output_config["effort"] = self.effort
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": strict_schema(json_schema)}
        if output_config:
            params["output_config"] = output_config
        if self.fallbacks:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def chat(
        self,
        messages: list[ChatMessage],
        model: str,
        temperature: float = 0.7,  # ignorada: los modelos Claude actuales usan `effort`
        json_mode: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        import anthropic

        client = self._get_client()
        params = self._request(
            messages, model, kwargs.get("json_schema"), kwargs.get("max_tokens", self.max_tokens)
        )
        api = client.beta.messages if self.fallbacks else client.messages
        try:
            try:
                response = api.create(**params)
            except anthropic.BadRequestError as e:
                # Un esquema que la API no acepta no debe tumbar la llamada: se repite
                # sin salida nativa y se valida a posteriori. Solo si el 400 trata del
                # esquema; otro 400 (effort inválido, contexto demasiado largo) se
                # informa tal cual en lugar de enmascararse con un reintento.
                about_schema = any(
                    w in str(e.message).lower() for w in ("schema", "format", "output_config")
                )
                if (
                    about_schema
                    and "output_config" in params
                    and "format" in params["output_config"]
                ):
                    params["output_config"].pop("format")
                    if not params["output_config"]:
                        params.pop("output_config")
                    try:
                        response = api.create(**params)
                    except anthropic.BadRequestError as again:
                        raise LLMError(
                            f"Petición rechazada por Anthropic: {again.message}"
                        ) from again
                else:
                    raise LLMError(f"Petición rechazada por Anthropic: {e.message}") from e
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise ProviderUnavailableError(
                "Anthropic rechazó la clave: revísala en Ajustes → Proveedores."
            ) from e
        except anthropic.NotFoundError as e:
            raise LLMError(f"Modelo no encontrado en Anthropic: {model!r}") from e
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            raise ProviderUnavailableError(f"No se pudo conectar con Anthropic: {e}") from e
        except anthropic.APIStatusError as e:
            raise ProviderUnavailableError(
                f"Error {e.status_code} de Anthropic: {e.message}"
            ) from e
        except anthropic.AnthropicError as e:
            if "api_key" in str(e).lower() or "credential" in str(e).lower():
                raise ProviderUnavailableError(
                    "Falta la clave de Anthropic: añádela en Ajustes → Proveedores."
                ) from e
            raise LLMError(str(e)) from e

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise LLMError(f"El modelo rechazó la petición (categoría: {category or 'n/d'}).")
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        return LLMResponse(content=text, model=response.model, raw={"id": response.id})

    def list_models(self) -> list[str]:
        import anthropic

        try:
            return sorted(m.id for m in self._get_client().models.list())
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise ProviderUnavailableError(
                "Anthropic rechazó la clave: revísala en Ajustes → Proveedores."
            ) from e
        except anthropic.AnthropicError as e:
            raise ProviderUnavailableError(f"No se pudo listar modelos de Anthropic: {e}") from e
