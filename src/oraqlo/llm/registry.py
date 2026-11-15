"""Registro de proveedores: nombre de config → instancia de LLMProvider.

Nombres incorporados: `anthropic`, `ollama-local`, `ollama-cloud` y los presets
OpenAI-compatibles (`openai`, `gemini`, `openrouter`, `groq`, `deepseek`,
`mistral`, `xai`, `lmstudio`). La sección `[providers.<nombre>]` del TOML
añade proveedores propios o ajusta uno existente.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from oraqlo.config import OraqloConfig, ProviderSettings, ResolvedRole
from oraqlo.llm.base import LLMProvider
from oraqlo.llm.claude import AnthropicProvider
from oraqlo.llm.ollama import OllamaBackendConfig, OllamaProvider
from oraqlo.llm.openai_compat import PRESETS, OpenAICompatProvider

BUILTIN = ("anthropic", "ollama-local", "ollama-cloud", *PRESETS)


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    kind: str
    api_key_env: str | None

    @property
    def key_present(self) -> bool | None:
        """True/False si necesita clave y está/no está definida; None si no necesita."""
        if not self.api_key_env:
            return None
        return bool(os.environ.get(self.api_key_env))


def provider_info(name: str, config: OraqloConfig) -> ProviderInfo:
    custom = config.providers.get(name)
    if custom is not None:
        return ProviderInfo(name, custom.type, custom.api_key_env or _default_env(custom))
    if name == "anthropic":
        return ProviderInfo(name, "anthropic", "ANTHROPIC_API_KEY")
    if name == "ollama-local":
        return ProviderInfo(name, "ollama", None)
    if name == "ollama-cloud":
        return ProviderInfo(name, "ollama", "OLLAMA_API_KEY")
    if name in PRESETS:
        return ProviderInfo(name, "openai", PRESETS[name].api_key_env)
    raise KeyError(f"Proveedor desconocido: {name!r}. Incorporados: {BUILTIN}")


def _default_env(custom: ProviderSettings) -> str | None:
    if custom.type == "anthropic":
        return "ANTHROPIC_API_KEY"
    if custom.type == "openai" and custom.preset in PRESETS:
        return PRESETS[custom.preset].api_key_env
    return None


class ProviderRegistry:
    """Construye y cachea proveedores. Un AnthropicProvider por `effort` distinto."""

    def __init__(self, config: OraqloConfig) -> None:
        self.config = config
        self._cache: dict[tuple[str, str | None], LLMProvider] = {}

    def names(self) -> list[str]:
        return sorted({*BUILTIN, *self.config.providers})

    def get(self, name: str, effort: str | None = None) -> LLMProvider:
        key = (name, effort if self._kind(name) == "anthropic" else None)
        if key not in self._cache:
            self._cache[key] = self._build(name, effort)
        return self._cache[key]

    def for_role(self, role: str) -> tuple[LLMProvider, ResolvedRole]:
        resolved = self.config.role(role)
        return self.get(resolved.provider, resolved.effort), resolved

    def _kind(self, name: str) -> str:
        return provider_info(name, self.config).kind

    def _build(self, name: str, effort: str | None) -> LLMProvider:
        custom = self.config.providers.get(name)
        if custom is not None:
            if custom.type == "anthropic":
                return AnthropicProvider(
                    api_key_env=custom.api_key_env or "ANTHROPIC_API_KEY", effort=effort
                )
            if custom.type == "ollama":
                cfg = OllamaBackendConfig(
                    host=custom.base_url or "http://localhost:11434", api_key_env=custom.api_key_env
                )
                return OllamaProvider(cfg)
            provider = OpenAICompatProvider(
                preset=custom.preset or name,
                base_url=custom.base_url,
                api_key_env=custom.api_key_env,
            )
            provider.name = name
            return provider
        if name == "anthropic":
            return AnthropicProvider(effort=effort)
        if name == "ollama-local":
            return OllamaProvider(OllamaBackendConfig.local())
        if name == "ollama-cloud":
            return OllamaProvider(OllamaBackendConfig.cloud())
        if name in PRESETS:
            return OpenAICompatProvider(preset=name)
        raise KeyError(f"Proveedor desconocido: {name!r}. Incorporados: {BUILTIN}")
