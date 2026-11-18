"""Configuración editable desde la app: claves de API y `oraqlo.toml`.

Antes había que crear estos archivos a mano. Ahora la app los escribe, siempre
desde el propio PC (la API exige acceso local para estas rutas):

- Todas las claves se ponen en Ajustes. La app las guarda en `<datos>/.env` (un
  detalle interno que nadie tiene que editar), escrito de forma atómica. Nunca salen
  por la API completas: solo una pista (`sk-…abcd`).
- Las APIs propias (`[providers.<nombre>]`) también se crean y borran desde Ajustes.
- `oraqlo.toml` se edita con tomlkit, que conserva los comentarios y lo que el
  usuario haya escrito a mano. Antes de guardar se valida con el mismo cargador
  que usa el servidor, así un cambio inválido nunca deja la config rota.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import tomlkit
from dotenv import dotenv_values

from oraqlo.config import ROLES, OraqloConfig, config_path, load_config

ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


class ConfigError(ValueError):
    """Cambio de configuración rechazado (el mensaje se muestra al usuario)."""


# ── claves (.env) ──────────────────────────────────────────────────────────


def key_hint(value: str | None) -> str | None:
    """Pista para reconocer una clave sin mostrarla: `sk-a…wxyz`."""
    if not value:
        return None
    value = value.strip()
    if len(value) <= 8:
        return "…" + value[-2:]
    return f"{value[:4]}…{value[-4:]}"


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_secret(home: Path, name: str, value: str | None, allowed: Iterable[str]) -> None:
    """Guarda (o borra, con value=None) una clave en <home>/.env y en el entorno actual."""
    if name not in set(allowed) or not ENV_NAME.match(name):
        raise ConfigError(f"{name} no es una clave de ningún proveedor conocido.")
    if value is not None:
        value = value.strip()
        if not value or any(c in value for c in "\r\n"):
            raise ConfigError("La clave está vacía o tiene saltos de línea.")
    path = home / ".env"
    current = dict(dotenv_values(path)) if path.exists() else {}
    if value is None:
        current.pop(name, None)
        os.environ.pop(name, None)
    else:
        current[name] = value
        os.environ[name] = value
    lines = [
        "# Claves de API de Oraqlo. Las escribe la app (Ajustes > Proveedores).",
        *(f"{k}={_quote(v)}" for k, v in current.items() if v is not None),
    ]
    _write_atomic(path, "\n".join(lines) + "\n")


# ── oraqlo.toml ────────────────────────────────────────────────────────────

ROLE_FIELDS = ("provider", "model", "temperature", "effort")
PROVIDER_NAME = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
PROVIDER_TYPES = ("openai", "anthropic", "ollama")


def provider_key_env(name: str) -> str:
    """Variable (interna) donde se guarda la clave de una API propia."""
    return "ORAQLO_KEY_" + name.upper().replace("-", "_")


def check_provider_name(name: str) -> None:
    from oraqlo.llm.registry import BUILTIN

    if not PROVIDER_NAME.match(name):
        raise ConfigError(
            "El nombre debe empezar por una letra y llevar solo minúsculas, números y guiones "
            "(de 2 a 31 caracteres)."
        )
    if name in BUILTIN:
        raise ConfigError(f"«{name}» ya es un proveedor de Oraqlo: elige otro nombre.")


def read_document(home: Path) -> tomlkit.TOMLDocument:
    path = config_path(home)
    if not path.exists():
        return tomlkit.document()
    return tomlkit.parse(path.read_text(encoding="utf-8-sig"))


def _table(doc: Any, key: str) -> Any:
    if key not in doc:
        doc[key] = tomlkit.table()
    return doc[key]


def apply_changes(home: Path, changes: dict[str, Any]) -> OraqloConfig:
    """Aplica cambios al toml, lo valida y lo guarda. Devuelve la config nueva.

    `changes` admite:
    - `roles`: {rol: {provider, model, temperature, effort}}; un valor None o ""
      quita ese ajuste (vuelve al heredado); un rol vacío se elimina.
    - `updates_mode`: "auto" | "notify" | "off".
    - `allowed_users`: lista de logins de Tailscale.
    - `committee`: modelos del comité (lista; [] = sin comité) o None = los recomendados.
    - `providers`: {nombre: {type, base_url, api_key_env} | None}; None borra la API propia.
    """
    doc = read_document(home)
    roles = changes.get("roles")
    if roles:
        table = _table(doc, "roles")
        for role, fields in roles.items():
            if role != "default" and role not in ROLES:
                raise ConfigError(f"Rol desconocido: {role}.")
            if role not in table:
                table[role] = tomlkit.table()
            entry = table[role]
            for field, value in (fields or {}).items():
                if field not in ROLE_FIELDS:
                    raise ConfigError(f"Ajuste de rol desconocido: {field}.")
                if value is None or value == "":
                    entry.pop(field, None)
                else:
                    entry[field] = value
            if not entry:
                del table[role]
    if "committee" in changes:
        # None: los recomendados (se quita el ajuste); lista: esos modelos ([] = sin comité).
        committee = changes["committee"]
        forecast = _table(doc, "forecast")
        if committee is None:
            forecast.pop("committee", None)
            if not forecast:
                del doc["forecast"]
        else:
            forecast["committee"] = [str(m).strip() for m in committee if str(m).strip()]
    for name, spec in (changes.get("providers") or {}).items():
        check_provider_name(name)
        table = _table(doc, "providers")
        if spec is None:
            table.pop(name, None)
            if not table:
                del doc["providers"]
            continue
        kind = spec.get("type")
        if kind not in PROVIDER_TYPES:
            raise ConfigError(f"Tipo de API desconocido: {kind}.")
        base_url = str(spec.get("base_url") or "").strip()
        if kind != "anthropic" and not base_url.startswith(("http://", "https://")):
            raise ConfigError("La URL debe empezar por http:// o https://.")
        entry = tomlkit.table()
        entry["type"] = kind
        if base_url:
            entry["base_url"] = base_url
        if spec.get("api_key_env"):
            entry["api_key_env"] = spec["api_key_env"]
        table[name] = entry
    if "updates_mode" in changes:
        _table(doc, "updates")["mode"] = changes["updates_mode"]
    if "allowed_users" in changes:
        users = [u.strip() for u in changes["allowed_users"] or [] if u and u.strip()]
        _table(doc, "server")["allowed_users"] = users

    path = config_path(home)
    text = tomlkit.dumps(doc)
    # Validación con el cargador real antes de tocar el archivo.
    fd, tmp = tempfile.mkstemp(dir=home if home.exists() else None, suffix=".toml")
    os.close(fd)
    try:
        Path(tmp).write_text(text, encoding="utf-8")
        try:
            config = load_config(Path(tmp), home=home)
        except Exception as e:
            raise ConfigError(f"Configuración inválida: {e}") from e
    finally:
        Path(tmp).unlink(missing_ok=True)
    _write_atomic(path, text)
    return config


def toml_roles(home: Path) -> dict[str, dict[str, Any]]:
    """Lo que el usuario fijó explícitamente por rol (sin lo heredado)."""
    roles = read_document(home).unwrap().get("roles", {})
    return {name: dict(t) for name, t in roles.items() if isinstance(t, dict)}
