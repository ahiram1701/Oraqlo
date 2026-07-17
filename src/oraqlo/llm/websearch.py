"""Cliente de búsqueda web de Ollama: los ojos de Oraqlo.

Usa https://ollama.com/api/web_search y /api/web_fetch con la misma
OLLAMA_API_KEY del backend cloud — sin cuentas ni dependencias nuevas.
Esquemas verificados contra la API real:
  web_search → {"results": [{"title", "url", "content"}, ...]}
  web_fetch  → {"title", "content", "links"}
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from oraqlo.llm.ollama import OllamaUnavailableError

SEARCH_HOST = "https://ollama.com"


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class OllamaWebClient:
    """search()/fetch() contra la API web de Ollama, con errores accionables."""

    def __init__(self, api_key_env: str = "OLLAMA_API_KEY", timeout_s: float = 30.0) -> None:
        self.api_key_env = api_key_env
        self.timeout_s = timeout_s

    def is_available(self) -> bool:
        """True si hay API key en el entorno (la búsqueda siempre es cloud)."""
        return bool(os.environ.get(self.api_key_env))

    def _post(self, path: str, payload: dict) -> dict:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise OllamaUnavailableError(
                f"La búsqueda web necesita la variable de entorno {self.api_key_env} "
                f"(API key de ollama.com)."
            )
        request = urllib.request.Request(
            url=f"{SEARCH_HOST}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            if e.code == 429:
                raise OllamaUnavailableError(
                    "Límite de cuota de búsqueda web de Ollama alcanzado; reintenta más tarde."
                ) from e
            if e.code in (401, 403):
                raise OllamaUnavailableError(
                    f"Autenticación rechazada por la búsqueda web (HTTP {e.code}); "
                    f"revisa {self.api_key_env}."
                ) from e
            raise OllamaUnavailableError(f"Error HTTP {e.code} en {path}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise OllamaUnavailableError(f"No se pudo conectar con la búsqueda web: {e}") from e

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        data = self._post("/api/web_search", {"query": query, "max_results": max_results})
        results = []
        for item in data.get("results", []):
            if not isinstance(item, dict) or not item.get("url"):
                continue
            results.append(
                SearchResult(
                    title=str(item.get("title", "")).strip(),
                    url=str(item["url"]),
                    snippet=str(item.get("content", ""))[:1500],
                )
            )
        return results[:max_results]

    def fetch(self, url: str, max_chars: int = 6000) -> str:
        """Contenido textual de una página, truncado (las páginas pueden ser enormes)."""
        data = self._post("/api/web_fetch", {"url": url})
        content = str(data.get("content", ""))
        title = str(data.get("title", "")).strip()
        text = f"{title}\n\n{content}" if title else content
        return text[:max_chars]
