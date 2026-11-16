"""Puerto local y publicación en la tailnet.

Oraqlo escucha en 127.0.0.1:<puerto> y `tailscale serve` lo publica por HTTPS
en la tailnet. El puerto lo decide el lanzador (app/port.txt): si otra
aplicación ocupa el elegido, salta al siguiente libre. Este módulo mantiene
`tailscale serve` apuntando al puerto real, de modo que la URL del celular
(https://<pc>.<tailnet>.ts.net:8443) no cambia aunque cambie el puerto. Oraqlo
usa su propio puerto HTTPS de la tailnet y nunca la raíz: esa es de otras apps.
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


HTTPS_PORT = 8443  # puerto propio en la tailnet: la raíz (443) queda para otras apps


def published_https(app_dir: Path) -> int | None:
    """Puerto HTTPS de la tailnet donde Oraqlo se publicó, o None.

    `tailscale.txt` guarda ese puerto. El valor antiguo "on" (hasta la 0.5) significa
    que se publicó en la raíz 443, que hay que migrar para no pisar otras apps.
    """
    try:
        value = (app_dir / TAILSCALE_FILE).read_text(encoding="utf-8-sig").strip()
    except OSError:
        return None
    if value == "on":
        return 443
    return int(value) if value.isdigit() else None


def set_published_https(app_dir: Path, https: int | None) -> None:
    path = app_dir / TAILSCALE_FILE
    if https is None:
        path.unlink(missing_ok=True)
    else:
        app_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(str(https), encoding="utf-8")


def tailscale_enabled(app_dir: Path) -> bool:
    return published_https(app_dir) is not None


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
    """Lee y ajusta `tailscale serve` (HTTPS :8443 → http://127.0.0.1:<puerto>)."""

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

    def url(self, status: dict | None = None, https: int = HTTPS_PORT) -> str | None:
        """https://<pc>.<tailnet>.ts.net[:puerto], si Tailscale está conectado."""
        status = self.status() if status is None else status
        dns = str(status.get("Self", {}).get("DNSName", "")).rstrip(".")
        if not dns:
            return None
        return f"https://{dns}" if https == 443 else f"https://{dns}:{https}"

    def _serve_config(self) -> dict:
        return self._json("serve", "status")

    def proxies(self, config: dict | None = None) -> dict[int, int]:
        """Puerto HTTPS de la tailnet → puerto local al que apunta su raíz "/"."""
        config = self._serve_config() if config is None else config
        found: dict[int, int] = {}
        for host, conf in (config.get("Web") or {}).items():
            _, _, https = str(host).rpartition(":")
            if not https.isdigit():
                continue
            proxy = ((conf or {}).get("Handlers", {}).get("/") or {}).get("Proxy", "")
            parts = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
            if parts.hostname in ("127.0.0.1", "localhost") and parts.port:
                found[int(https)] = parts.port
        return found

    def serving_port(self, https: int = HTTPS_PORT) -> int | None:
        """Puerto local publicado en ese puerto HTTPS de la tailnet, o None."""
        return self.proxies().get(https)

    def free_https_port(self, local_port: int, start: int = HTTPS_PORT) -> int:
        """`start` si está libre (o ya es nuestro); si otra app lo usa, el siguiente."""
        config = self._serve_config()
        proxies = self.proxies(config)
        taken = {int(p) for p in (config.get("TCP") or {}) if str(p).isdigit()}
        for candidate in range(start, start + 50):
            ours = proxies.get(candidate) == local_port
            if ours or (candidate not in proxies and candidate not in taken):
                return candidate
        return start

    def disable(self, https: int = HTTPS_PORT) -> bool:
        """Retira la publicación de ese puerto HTTPS (solo ese)."""
        if not self.exe:
            return False
        try:
            out = self.run([self.exe, "serve", f"--https={https}", "off"])
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("No pude retirar tailscale serve: %s", e)
            return False
        return out.returncode == 0

    def ensure(self, port: int, https: int = HTTPS_PORT) -> bool:
        """Publica 127.0.0.1:<port> en <https> si hoy apunta a otro. True si cambió."""
        if not self.exe or self.serving_port(https) == port:
            return False
        try:
            out = self.run(
                [self.exe, "serve", "--bg", f"--https={https}", f"http://127.0.0.1:{port}"]
            )
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("No pude actualizar tailscale serve: %s", e)
            return False
        if out.returncode != 0:
            log.warning("tailscale serve falló: %s", (out.stderr or out.stdout).strip())
            return False
        log.info("tailscale serve: :%d → 127.0.0.1:%d", https, port)
        return True

    def migrate_from_root(self, app_dir: Path, local_port: int) -> int | None:
        """Hasta la 0.5 Oraqlo se publicaba en la raíz (443) y pisaba a otras apps.
        Retira SOLO su propio handler de la raíz y se publica en su puerto propio.
        Devuelve el puerto HTTPS nuevo, o None si no había nada que migrar."""
        if published_https(app_dir) != 443:
            return None
        if self.serving_port(443) == local_port:
            self.disable(443)
        https = self.free_https_port(local_port)
        self.ensure(local_port, https)
        if self.serving_port(https) != local_port:
            return None
        set_published_https(app_dir, https)
        log.info("Oraqlo deja la raíz de la tailnet y pasa a :%d", https)
        return https
