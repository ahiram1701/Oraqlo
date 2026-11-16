"""Lanzador de Oraqlo: arranca el servidor, lo vigila y revierte actualizaciones rotas.

SOLO librería estándar y sin importar nada de `oraqlo`: el instalador copia este
archivo fuera de las versiones (<home>/app/launcher.py) para que siga
funcionando aunque una versión nueva esté rota. Lo ejecuta la tarea programada
de Windows al iniciar sesión:

    pythonw.exe <home>\\app\\launcher.py --home <home> --port 8765

Bucle:
1. Lee current.txt y el puerto (port.txt; si otra aplicación lo ocupa, salta al
   siguiente libre y lo guarda) y arranca `versions/<v>/.../python -m oraqlo.cli serve`.
2. Si la versión está "pendiente" (recién actualizada), exige /api/health en 60 s;
   si no responde, la mata y vuelve a previous.txt (rollback automático).
3. Si el servidor sale con 75 → hay versión nueva: relanza. Con 0 → fin.
   Con otro código → reintenta con espera creciente (máx. 5 caídas en 10 min).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

RESTART_EXIT_CODE = 75
HEALTH_TIMEOUT_S = 60.0
PORT_SCAN = 100  # puertos que se prueban tras el elegido si está ocupado


def port_busy(port: int) -> bool:
    """¿Algo escucha en 127.0.0.1:<port>, o no se puede ocupar?"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def is_oraqlo_health(body: bytes) -> bool:
    try:
        data = json.loads(body)
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("app") == "oraqlo" and data.get("status") == "ok"


class Launcher:
    def __init__(
        self,
        home: Path,
        port: int = 8765,
        spawn: Callable[[list[str]], subprocess.Popen] | None = None,
        healthy: Callable[[int], bool] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        health_timeout_s: float = HEALTH_TIMEOUT_S,
        busy: Callable[[int], bool] = port_busy,
    ) -> None:
        self.home = Path(home)
        self.root = self.home / "app"
        self.port = port
        self.spawn = spawn or self._spawn
        self.healthy = healthy or self._healthy
        self.sleep = sleep
        self.health_timeout_s = health_timeout_s
        self.busy = busy
        self.crashes: list[float] = []

    # ── archivos de estado ────────────────────────────────────────────────

    def read(self, name: str) -> str | None:
        path = self.root / name
        if not path.exists():
            return None
        # utf-8-sig: tolera el BOM que añade PowerShell 5.1.
        return path.read_text(encoding="utf-8-sig").strip() or None

    def write(self, name: str, value: str | None) -> None:
        path = self.root / name
        if value is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(value, encoding="utf-8")

    def log(self, message: str) -> None:
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        with (self.root / "update.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} [lanzador] {message}\n")

    # ── puerto ────────────────────────────────────────────────────────────

    def resolve_port(self) -> int:
        """Puerto de port.txt (o --port). Si otra app lo ocupa, el siguiente libre."""
        saved = self.read("port.txt")
        preferred = int(saved) if saved and saved.isdigit() else self.port
        # Unos segundos de gracia: tras un reinicio, el proceso anterior suelta el puerto.
        for _ in range(5):
            if not self.busy(preferred):
                break
            self.sleep(1.0)
        else:
            for candidate in range(preferred + 1, min(preferred + PORT_SCAN, 65535) + 1):
                if not self.busy(candidate):
                    self.log(f"el puerto {preferred} lo usa otra aplicación; uso {candidate}")
                    preferred = candidate
                    break
            else:
                self.log(f"no encontré puerto libre tras {preferred}; lo intento igualmente")
        if saved != str(preferred):
            self.write("port.txt", str(preferred))
        self.port = preferred
        return preferred

    # ── procesos ──────────────────────────────────────────────────────────

    def python(self, version: str) -> Path:
        scripts, exe = ("Scripts", "pythonw.exe") if os.name == "nt" else ("bin", "python")
        path = self.root / "versions" / version / scripts / exe
        if os.name == "nt" and not path.exists():
            path = path.with_name("python.exe")
        return path

    def command(self, version: str) -> list[str]:
        return [str(self.python(version)), "-m", "oraqlo.cli", "serve", "--port", str(self.port)]

    def _spawn(self, cmd: list[str]) -> subprocess.Popen:
        env = dict(os.environ, ORAQLO_HOME=str(self.home))
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        logfile = (self.root / "server.log").open("a", encoding="utf-8")
        return subprocess.Popen(
            cmd, env=env, stdout=logfile, stderr=subprocess.STDOUT, creationflags=flags
        )

    def _healthy(self, port: int) -> bool:
        """True solo si en el puerto responde Oraqlo (otra app con /api/health no cuenta)."""
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as r:
                return r.status == 200 and is_oraqlo_health(r.read())
        except OSError:
            return False

    def wait_healthy(self, proc: subprocess.Popen) -> bool:
        waited = 0.0
        while waited < self.health_timeout_s:
            if proc.poll() is not None:
                return False
            if self.healthy(self.port):
                return True
            self.sleep(1.0)
            waited += 1.0
        return False

    def rollback(self, broken: str) -> str | None:
        previous = self.read("previous.txt")
        if not previous or previous == broken:
            self.log(f"{broken} no arranca y no hay versión anterior a la que volver")
            return None
        self.write("current.txt", previous)
        self.write("previous.txt", broken)
        self.write("pending.txt", None)
        self.write("rolled_back.txt", f"{broken} → {previous}")
        self.log(f"ROLLBACK automático: {broken} no respondió; vuelvo a {previous}")
        return previous

    # ── bucle principal ───────────────────────────────────────────────────

    def run_once(self) -> int | None:
        """Una vida del servidor. Devuelve su código de salida (None = rollback hecho)."""
        version = self.read("current.txt")
        if not version:
            raise SystemExit("No hay current.txt: reinstala Oraqlo.")
        pending = self.read("pending.txt") == version
        self.resolve_port()
        proc = self.spawn(self.command(version))
        if pending:
            if not self.wait_healthy(proc):
                proc.kill()
                proc.wait()
                if self.rollback(version) is None:
                    return 1
                return None
            self.write("pending.txt", None)
            self.log(f"{version} arrancó correctamente")
        return proc.wait()

    def run(self) -> int:
        while True:
            code = self.run_once()
            if code is None or code == RESTART_EXIT_CODE:
                continue
            if code == 0:
                return 0
            now = time.monotonic()
            self.crashes = [t for t in self.crashes if now - t < 600] + [now]
            if len(self.crashes) > 5:
                self.log("demasiadas caídas en 10 minutos; me detengo")
                return code
            self.log(f"el servidor terminó con código {code}; reinicio")
            self.sleep(min(60.0, 2.0 ** len(self.crashes)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lanzador de Oraqlo (instalación gestionada).")
    parser.add_argument(
        "--home", default=os.environ.get("ORAQLO_HOME"), required="ORAQLO_HOME" not in os.environ
    )
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    return Launcher(Path(args.home), port=args.port).run()


if __name__ == "__main__":
    sys.exit(main())
