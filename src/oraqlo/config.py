"""Configuración de Oraqlo: `oraqlo.toml` + variables de entorno (.env).

Ubicaciones (en orden de prioridad):
- Directorio de datos: `ORAQLO_HOME`, o el estándar del SO
  (Windows: %LOCALAPPDATA%\\Oraqlo; Linux: ~/.local/share/oraqlo).
- Config: `ORAQLO_CONFIG`, o `<datos>/oraqlo.toml`. Sin archivo, valores por defecto.
- Secretos: SOLO por variables de entorno (o `<datos>/.env`). Nunca en el TOML.

Cada rol del panel (estratega, red_team, oraculo, juez) y de las tareas
auxiliares (investigador, redactor, practica) se resuelve como
`roles.<rol>` fusionado sobre `roles.default`.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ROLES = ("estratega", "red_team", "oraculo", "juez", "investigador", "redactor", "practica")

# Temperaturas por defecto por rol (para backends que la admiten; Claude usa `effort`).
DEFAULT_TEMPERATURES = {
    "estratega": 0.7,
    "red_team": 0.9,
    "oraculo": 0.2,
    "juez": 0.1,
    "investigador": 0.3,
    "redactor": 0.3,
    "practica": 0.2,
}


class RoleSettings(BaseModel):
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None


class ResolvedRole(BaseModel):
    name: str
    provider: str
    model: str
    temperature: float
    effort: str | None = None


class ProviderSettings(BaseModel):
    """Proveedor personalizado: un preset existente con otra URL/clave, o uno nuevo."""

    type: Literal["anthropic", "openai", "ollama"]
    preset: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765
    # Logins de Tailscale autorizados (vacío = cualquier usuario de tu tailnet).
    allowed_users: list[str] = Field(default_factory=list)


class UpdateSettings(BaseModel):
    mode: Literal["auto", "notify", "off"] = "auto"
    channel: Literal["stable", "beta"] = "stable"
    repo: str = "ahiram1701/Oraqlo"
    interval_hours: float = 6.0


class PracticeSettings(BaseModel):
    auto: bool = False
    interval_min: float = 60.0
    markets: int = 2


class OraqloConfig(BaseModel):
    home: Path
    roles: dict[str, RoleSettings] = Field(default_factory=dict)
    providers: dict[str, ProviderSettings] = Field(default_factory=dict)
    server: ServerSettings = Field(default_factory=ServerSettings)
    updates: UpdateSettings = Field(default_factory=UpdateSettings)
    practice: PracticeSettings = Field(default_factory=PracticeSettings)

    @property
    def db_path(self) -> Path:
        return self.home / "data" / "oraqlo.db"

    @property
    def reports_dir(self) -> Path:
        return self.home / "informes"

    @property
    def backups_dir(self) -> Path:
        return self.home / "backups"

    def role(self, name: str) -> ResolvedRole:
        """Rol efectivo: `roles.<name>` sobre `roles.default` sobre la detección automática."""
        auto_provider, auto_model = default_provider_and_model()
        base = self.roles.get("default", RoleSettings())
        own = self.roles.get(name, RoleSettings())
        base_provider = base.provider or auto_provider
        provider = own.provider or base_provider
        # El modelo de `default` solo se hereda si el rol usa el mismo proveedor.
        inherited = base.model if provider == base_provider else None
        model = own.model or inherited or default_model_for(provider) or auto_model
        temperature = own.temperature
        if temperature is None:
            temperature = DEFAULT_TEMPERATURES.get(name, base.temperature or 0.5)
        return ResolvedRole(
            name=name,
            provider=provider,
            model=model,
            temperature=temperature,
            effort=own.effort or base.effort,
        )


def default_model_for(provider: str) -> str:
    return {
        "anthropic": "claude-opus-5-5",
        "ollama-cloud": "gpt-oss:120b-cloud",
        "ollama-local": "llama3.1",
        "openai": "gpt-5",
        "gemini": "gemini-2.5-pro",
        "openrouter": "anthropic/claude-opus-5-5",
        "groq": "llama-3.3-70b-versatile",
        "deepseek": "deepseek-chat",
        "mistral": "mistral-large-latest",
        "xai": "grok-4",
        "lmstudio": "local-model",
    }.get(provider, "")


# Orden de preferencia cuando no hay `roles.default.provider`: la primera clave presente.
AUTO_PROVIDERS = (
    ("anthropic", "ANTHROPIC_API_KEY"),
    ("openai", "OPENAI_API_KEY"),
    ("gemini", "GEMINI_API_KEY"),
    ("openrouter", "OPENROUTER_API_KEY"),
    ("ollama-cloud", "OLLAMA_API_KEY"),
    ("mistral", "MISTRAL_API_KEY"),
    ("xai", "XAI_API_KEY"),
    ("deepseek", "DEEPSEEK_API_KEY"),
    ("groq", "GROQ_API_KEY"),
)


def default_provider_and_model() -> tuple[str, str]:
    """Sin configuración explícita, elige según las claves disponibles en el entorno."""
    for provider, env in AUTO_PROVIDERS:
        if os.environ.get(env):
            return provider, default_model_for(provider)
    return "ollama-local", default_model_for("ollama-local")


def default_home() -> Path:
    env = os.environ.get("ORAQLO_HOME")
    if env:
        return Path(env).expanduser()
    from platformdirs import user_data_dir

    return Path(user_data_dir("Oraqlo", appauthor=False))


def config_path(home: Path | None = None) -> Path:
    env = os.environ.get("ORAQLO_CONFIG")
    return Path(env).expanduser() if env else (home or default_home()) / "oraqlo.toml"


def load_config(path: Path | None = None, home: Path | None = None) -> OraqloConfig:
    """Carga la configuración (y el .env del directorio de datos y del CWD)."""
    from dotenv import load_dotenv

    home = home or default_home()
    load_dotenv(home / ".env", override=False)
    load_dotenv(Path.cwd() / ".env", override=False)
    path = path or config_path(home)
    data: dict = {}
    if path.exists():
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    unknown = {k: v for k, v in data.get("roles", {}).items() if k != "default" and k not in ROLES}
    if unknown:
        raise ValueError(f"Roles desconocidos en {path}: {sorted(unknown)}; válidos: {ROLES}")
    return OraqloConfig(home=home, **data)
