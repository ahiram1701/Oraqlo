"""Puerto local y publicación en la tailnet.

Oraqlo escucha en 127.0.0.1:<puerto> y `tailscale serve` lo publica por HTTPS
en la tailnet. El puerto lo decide el lanzador (app/port.txt): si otra
aplicación ocupa el elegido, salta al siguiente libre. Este módulo mantiene
`tailscale serve` apuntando al puerto real, de modo que la URL del celular
(https://<pc>.<tailnet>.ts.net) no cambia aunque cambie el puerto.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

PORT_FILE = "port.txt"
TAILSCALE_FILE = "tailscale.txt"  # "on" = el instalador publicó Oraqlo en la tailnet
MIN_PORT, MAX_PORT = 1024, 65535


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """¿Hay algo escuchando en el puerto, o no se puede ocupar?"""
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return True
    return False


def read_port_file(app_dir: Path) -> int | None:
    path = app_dir / PORT_FILE
    try:
        port = int(path.read_text(encoding="utf-8-sig").strip())
    except (OSError, ValueError):
        return None
    return port if MIN_PORT <= port <= MAX_PORT else None


def write_port_file(app_dir: Path, port: int) -> None:
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / PORT_FILE).write_text(str(port), encoding="utf-8")


def set_tailscale_enabled(app_dir: Path, enabled: bool) -> None:
    path = app_dir / TAILSCALE_FILE
    if enabled:
        app_dir.mkdir(parents=True, exist_ok=True)
        path.write_text("on", encoding="utf-8")
    else:
        path.unlink(missing_ok=True)


def tailscale_enabled(app_dir: Path) -> bool:
    try:
        return (app_dir / TAILSCALE_FILE).read_text(encoding="utf-8-sig").strip() == "on"
    except OSError:
        return False


Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", timeout=20, creationflags=flags
    )


def find_tailscale() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    default = (
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Tailscale" / "tailscale.exe"
    )
    return str(default) if default.exists() else None


class TailscaleServe:
    """Lee y ajusta `tailscale serve` (HTTPS 443 → http://127.0.0.1:<puerto>)."""

    def __init__(self, exe: str | None = None, run: Runner = _run) -> None:
        self.exe = exe if exe is not None else find_tailscale()
        self.run = run

    @property
    def installed(self) -> bool:
        return bool(self.exe)

    def _json(self, *args: str) -> dict:
        if not self.exe:
            return {}
        try:
            out = self.run([self.exe, *args, "--json"])
        except (OSError, subprocess.SubprocessError):
            return {}
        if out.returncode != 0 or not out.stdout.strip():
            return {}
        try:
            data = json.loads(out.stdout)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def status(self) -> dict:
        return self._json("status")

    def state(self, status: dict | None = None) -> str:
        """ "missing" (no instalado), "stopped", "needs_login" o "running"."""
        if not self.exe:
            return "missing"
        status = self.status() if status is None else status
        backend = str(status.get("BackendState", ""))
        if backend == "Running":
            return "running"
        if backend in ("NeedsLogin", "NeedsMachineAuth", "NoState"):
            return "needs_login"
        return "stopped"

    def url(self, status: dict | None = None) -> str | None:
        """https://<pc>.<tailnet>.ts.net, si Tailscale está conectado."""
        status = self.status() if status is None else status
        dns = str(status.get("Self", {}).get("DNSName", "")).rstrip(".")
        return f"https://{dns}" if dns else None

    def disable(self) -> bool:
        """Retira la publicación HTTPS (443) de `tailscale serve`."""
        if not self.exe:
            return False
        try:
            out = self.run([self.exe, "serve", "--https=443", "off"])
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("No pude retirar tailscale serve: %s", e)
            return False
        return out.returncode == 0

    def serving_port(self) -> int | None:
        """Puerto local al que apunta la raíz HTTPS publicada, o None."""
        web = self._json("serve", "status").get("Web", {})
        for host, conf in web.items():
            if not str(host).endswith(":443"):
                continue
            proxy = (conf.get("Handlers", {}).get("/") or {}).get("Proxy", "")
            parts = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
            if parts.hostname in ("127.0.0.1", "localhost") and parts.port:
                return parts.port
        return None

    def ensure(self, port: int) -> bool:
        """Publica el puerto si hoy apunta a otro. True si cambió algo."""
        if not self.exe or self.serving_port() == port:
            return False
        try:
            out = self.run([self.exe, "serve", "--bg", str(port)])
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("No pude actualizar tailscale serve: %s", e)
            return False
        if out.returncode != 0:
            log.warning("tailscale serve falló: %s", (out.stderr or out.stdout).strip())
            return False
        log.info("tailscale serve ahora apunta a 127.0.0.1:%d", port)
        return True
