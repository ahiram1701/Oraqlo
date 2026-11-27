"""Autenticación delegada en Tailscale.

El servidor escucha SOLO en 127.0.0.1. Hay dos caminos para llegar:
1. Desde este PC (navegador local): sin cabeceras de proxy → permitido.
2. Desde el tailnet vía `tailscale serve`: Tailscale autentica al dispositivo
   e inyecta `Tailscale-User-Login` con la identidad real del usuario.

Cualquier petición que llegue *a través de un proxy* (X-Forwarded-For) sin
identidad de Tailscale se rechaza: así una exposición accidental con
`tailscale funnel` (internet público, sin identidad) no abre la app a nadie.
Si `server.allowed_users` no está vacío, solo esos logins entran.
"""

from __future__ import annotations

from dataclasses import dataclass

from starlette.requests import Request

LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


@dataclass(frozen=True)
class Identity:
    login: str | None
    name: str | None
    via: str  # "local" | "tailscale"


class AuthError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _hostname(value: str) -> str:
    """Host sin puerto (admite [::1]:8766)."""
    value = value.strip().lower()
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def check_request(request: Request) -> None:
    """Defensas para que una página web cualquiera abierta en este PC no pueda usar
    Oraqlo haciéndose pasar por el usuario local:

    - Host: solo 127.0.0.1/localhost o el nombre de la tailnet (*.ts.net). Una web
      con DNS rebinding llega con su propio dominio y se rechaza.
    - Origen: lo que cambia algo (POST/PUT/DELETE) debe venir de la propia app.
      `Sec-Fetch-Site: cross-site` u `Origin` de otro sitio/puerto → 403.
    """
    host = request.headers.get("host", "")
    name = _hostname(host)
    if name not in LOCAL_HOSTS and not name.endswith(".ts.net"):
        raise AuthError(400, f"Host no permitido: {host or '(vacío)'}.")
    if request.method in SAFE_METHODS:
        return
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise AuthError(403, "Petición bloqueada: viene de otro sitio web.")
    origin = request.headers.get("origin")
    if origin and origin != "null":
        authority = origin.split("://", 1)[-1].lower()
        if authority != host.lower():
            raise AuthError(403, "Petición bloqueada: viene de otro sitio web.")
    elif origin == "null":
        raise AuthError(403, "Petición bloqueada: origen desconocido.")


def identify(request: Request, allowed_users: list[str]) -> Identity:
    headers = request.headers
    login = headers.get("tailscale-user-login")
    if headers.get("tailscale-funnel-request"):
        raise AuthError(403, "Oraqlo no se sirve por Tailscale Funnel (internet público).")
    if login:
        if allowed_users and login not in allowed_users:
            raise AuthError(403, f"El usuario {login} no está autorizado en server.allowed_users.")
        return Identity(login=login, name=headers.get("tailscale-user-name"), via="tailscale")
    proxied = "x-forwarded-for" in headers or "forwarded" in headers
    client = request.client.host if request.client else ""
    if client in LOCAL_HOSTS and not proxied:
        return Identity(login=None, name=None, via="local")
    raise AuthError(401, "Acceso solo desde este equipo o desde tu tailnet (tailscale serve).")
