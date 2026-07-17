"""OllamaProvider: un solo protocolo contra dos backends — local y cloud (Turbo).

- Local:  http://localhost:11434, sin credenciales.
- Cloud:  https://ollama.com + API key leída de una variable de entorno
  (por defecto OLLAMA_API_KEY). La clave nunca se hardcodea ni se loguea.

Solo librería estándar (urllib): cero dependencias.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from oraqlo.llm.base import ChatMessage, LLMProvider, LLMResponse


class OllamaUnavailableError(RuntimeError):
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
    timeout_s: float = 120.0

    @classmethod
    def local(cls) -> OllamaBackendConfig:
        return cls(host="http://localhost:11434")

    @classmethod
    def cloud(cls, api_key_env: str = "OLLAMA_API_KEY") -> OllamaBackendConfig:
        return cls(host="https://ollama.com", api_key_env=api_key_env)


class OllamaProvider(LLMProvider):
    """Cliente del endpoint /api/chat de Ollama (idéntico en local y cloud)."""

    def __init__(self, config: OllamaBackendConfig | None = None) -> None:
        self.config = config or OllamaBackendConfig.local()

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
    ) -> LLMResponse:
        payload: dict = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {"temperature": temperature},
        }
        if json_mode:
            payload["format"] = "json"

        request = urllib.request.Request(
            url=f"{self.config.host.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            if e.code in (401, 403):
                raise OllamaUnavailableError(
                    f"Autenticación rechazada por {self.config.host} (HTTP {e.code}). "
                    f"Revisa la API key en {self.config.api_key_env}."
                ) from e
            raise OllamaUnavailableError(
                f"Error HTTP {e.code} de {self.config.host}: {detail}"
            ) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise OllamaUnavailableError(
                f"No se pudo conectar con {self.config.host}. "
                f"¿Está Ollama corriendo? ({e})"
            ) from e

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
