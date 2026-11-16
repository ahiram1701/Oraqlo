"""OllamaProvider: un solo protocolo contra dos backends — local y cloud (Turbo).

- Local:  http://localhost:11434, sin credenciales.
- Cloud:  https://ollama.com + API key leída de una variable de entorno
  (por defecto OLLAMA_API_KEY). La clave nunca se hardcodea ni se loguea.

Solo librería estándar (urllib). Reintenta errores transitorios (timeouts,
429, 5xx) con backoff; los permanentes (401/403) fallan con mensaje accionable.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from oraqlo.llm.base import ChatMessage, LLMProvider, LLMResponse, ProviderUnavailableError
from oraqlo.llm.retry import TransientError, is_transient_status, retry_call


class OllamaUnavailableError(ProviderUnavailableError):
    """El backend no está accesible (Ollama apagado, sin red, o falta la API key)."""


@dataclass
class OllamaBackendConfig:
    """Configuración de un backend Ollama.

    Para cloud, `api_key_env` nombra la variable de entorno con la clave; si la
    variable no existe al hacer la llamada, se lanza OllamaUnavailableError con
    un mensaje accionable (nunca un stack trace criptográfico).
    """

    host: str = "http://localhost:11434"
    api_key_env: str | None = None
    timeout_s: float = 300.0
    attempts: int = 3

    @classmethod
    def local(cls) -> OllamaBackendConfig:
        return cls(host="http://localhost:11434")

    @classmethod
    def cloud(cls, api_key_env: str = "OLLAMA_API_KEY") -> OllamaBackendConfig:
        return cls(host="https://ollama.com", api_key_env=api_key_env)


class OllamaProvider(LLMProvider):
    """Cliente del endpoint /api/chat de Ollama (idéntico en local y cloud)."""

    supports_json_schema = True  # `format` acepta un JSON Schema completo

    def __init__(self, config: OllamaBackendConfig | None = None) -> None:
        self.config = config or OllamaBackendConfig.local()
        self.name = "ollama-cloud" if self.config.api_key_env else "ollama-local"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key_env:
            key = os.environ.get(self.config.api_key_env)
            if not key:
                raise OllamaUnavailableError(
                    f"Backend cloud configurado pero la variable de entorno "
                    f"{self.config.api_key_env} no está definida. "
                    f"Exporta tu API key de ollama.com antes de usar este backend."
                )
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def chat(
        self,
        messages: list[ChatMessage],
        model: str,
        temperature: float = 0.7,
        json_mode: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        payload: dict = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {"temperature": temperature},
        }
        schema = kwargs.get("json_schema")
        if schema is not None:
            payload["format"] = schema
        elif json_mode:
            payload["format"] = "json"
        if "max_tokens" in kwargs:
            payload["options"]["num_predict"] = kwargs["max_tokens"]

        request = urllib.request.Request(
            url=f"{self.config.host.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )

        def attempt() -> dict:
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout_s) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")[:500]
                if e.code in (401, 403):
                    if self.config.api_key_env:
                        hint = f"Revisa la clave de Ollama ({self.config.api_key_env}) en Ajustes."
                    else:  # Ollama local con un modelo *-cloud sin sesión iniciada
                        hint = "Inicia sesión en Ollama: abre la app de Ollama → Sign in."
                    raise OllamaUnavailableError(
                        f"Ollama rechazó la petición (HTTP {e.code}). {hint}"
                    ) from e
                error = OllamaUnavailableError(
                    f"Error HTTP {e.code} de {self.config.host}: {detail}"
                )
                # Un 500 del Ollama local (no pudo cargar el modelo, p. ej. sin memoria) no se
                # arregla reintentando: falla a la primera con su mensaje.
                local_failure = not self.config.api_key_env and e.code == 500
                if is_transient_status(e.code) and not local_failure:
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    raise TransientError(
                        str(error),
                        float(retry_after) if retry_after and retry_after.isdigit() else None,
                    ) from error
                raise error from e
            except TimeoutError as e:
                # El modelo tardó más de lo permitido: reintentar repetiría la misma
                # espera (hasta 15 min). Se falla una vez con un mensaje claro.
                raise OllamaUnavailableError(
                    f"El modelo tardó más de {self.config.timeout_s:.0f} s en responder. "
                    "Prueba un modelo más pequeño o más rápido en Ajustes."
                ) from e
            except (urllib.error.URLError, OSError) as e:
                if isinstance(getattr(e, "reason", None), TimeoutError):
                    raise OllamaUnavailableError(
                        f"El modelo tardó más de {self.config.timeout_s:.0f} s en responder. "
                        "Prueba un modelo más pequeño o más rápido en Ajustes."
                    ) from e
                error = OllamaUnavailableError(
                    f"No se pudo conectar con {self.config.host}. ¿Está Ollama abierto? ({e})"
                )
                raise TransientError(str(error)) from error

        body = retry_call(attempt, attempts=self.config.attempts)

        content = body.get("message", {}).get("content", "")
        return LLMResponse(content=content, model=body.get("model", model), raw=body)

    def _get_json(self, path: str, timeout_s: float = 5.0) -> dict:
        """GET autenticado a la API de Ollama; errores como OllamaUnavailableError."""
        request = urllib.request.Request(
            url=f"{self.config.host.rstrip('/')}{path}", headers=self._headers()
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            raise OllamaUnavailableError(
                f"No se pudo consultar {self.config.host}{path}: {e}"
            ) from e

    def is_available(self) -> bool:
        """Comprobación barata de disponibilidad (para avisar en vez de fallar)."""
        try:
            self._get_json("/api/tags")
            return True
        except OllamaUnavailableError:
            return False

    def list_models(self) -> list[str]:
        """Nombres de los modelos disponibles en el backend (GET /api/tags).

        Lanza OllamaUnavailableError si el backend no responde o falta la API key.
        """
        data = self._get_json("/api/tags")
        return sorted(
            str(m["name"]) for m in data.get("models", []) if isinstance(m, dict) and m.get("name")
        )
