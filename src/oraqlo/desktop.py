"""Oraqlo como app de escritorio de Windows: ventana propia + icono en la bandeja.

    pythonw -m oraqlo.desktop --home <home> [--path /objetivos/x]

La ventana es WebView2 (el motor de Edge que trae Windows) mostrando la misma
app web que sirve el servidor local; no hay una segunda interfaz que mantener.
El servidor lo sigue arrancando la tarea programada: cerrar la ventana no lo
para (los análisis y avisos siguen), solo la esconde en la bandeja.

Lo lanza `launcher.py --open` (accesos directos del menú Inicio, avisos). Si
algo falla aquí (sin WebView2, dependencias rotas), sale con FALLBACK_EXIT_CODE
y el lanzador abre el navegador como antes.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from oraqlo import __version__

log = logging.getLogger(__name__)

FALLBACK_EXIT_CODE = 3
TITLE = "Oraqlo"
APP_ID = "Oraqlo.Desktop"
MUTEX = "Local\\Oraqlo.Desktop"
BACKGROUND = "#0b0d12"  # mismo fondo que la app: sin destello al abrir
LIGHT_BACKGROUND = "#f6f7fb"
DEFAULT_SIZE = (1100, 780)
MIN_SIZE = (400, 600)
VERSION_CHECK_S = 120.0  # cada cuánto mira si el servidor ya es otra versión
MUTEX_WAIT_S = 30.0  # la ventana nueva espera a que la vieja suelte el mutex


# ── utilidades puras (testeables) ─────────────────────────────────────────


def read_port(home: Path, default: int = 8765) -> int:
    try:
        value = (home / "app" / "port.txt").read_text(encoding="utf-8-sig").strip()
        return int(value)
    except (OSError, ValueError):
        return default


def app_url(port: int, path: str | None = None) -> str:
    path = path or "/"
    if not path.startswith("/"):
        path = "/" + path
    return f"http://127.0.0.1:{port}{path}"


def load_geometry(home: Path) -> dict:
    try:
        data = json.loads((home / "app" / "window.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"width": DEFAULT_SIZE[0], "height": DEFAULT_SIZE[1]}
    geo = {
        "width": max(int(data.get("width", DEFAULT_SIZE[0])), MIN_SIZE[0]),
        "height": max(int(data.get("height", DEFAULT_SIZE[1])), MIN_SIZE[1]),
    }
    if isinstance(data.get("x"), int) and isinstance(data.get("y"), int):
        geo["x"], geo["y"] = data["x"], data["y"]
    return geo


def save_geometry(home: Path, geometry: dict) -> None:
    # Se guarda lo que llegó por los eventos resized/moved: leer window.width y
    # compañía dentro de `closing` bloquea el hilo de la ventana (WinForms Invoke).
    with contextlib.suppress(OSError):
        (home / "app" / "window.json").write_text(json.dumps(geometry), encoding="utf-8")


def _health(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            data = json.loads(r.read())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("app") == "oraqlo" else None


def healthy(port: int) -> bool:
    return _health(port) is not None


def server_version(port: int) -> str | None:
    data = _health(port)
    version = data.get("version") if data else None
    return version if isinstance(version, str) else None


def needs_restart(mine: str, served: str | None) -> bool:
    """La ventana es de otra versión que el servidor: tras una actualización."""
    return bool(served) and served != mine and "unknown" not in mine


def reload_url(current: str | None, port: int) -> str:
    """La misma pantalla, recargada desde el servidor (que pudo cambiar de puerto)."""
    parts = urllib.parse.urlsplit(current or "")
    path = parts.path or "/"
    return urllib.parse.urlunsplit(("http", f"127.0.0.1:{port}", path, parts.query, parts.fragment))


def relaunch_command(home: Path, hidden: bool) -> list[str] | None:
    """La ventana de la versión activa (current.txt), que espera a que esta se cierre."""
    from oraqlo.launcher import Launcher, desktop_command

    launcher = Launcher(home)
    if launcher.read("current.txt") in (None, __version__):
        return None  # nada nuevo instalado: relanzar daría la misma ventana
    cmd = desktop_command(launcher)
    if cmd is None:
        return None
    return [*cmd, "--after-update"] + (["--hidden"] if hidden else [])


# ── Windows (ctypes) ──────────────────────────────────────────────────────


def _single_instance() -> bool:
    """True si somos la primera ventana. El mutex vive mientras viva el proceso."""
    if os.name != "nt":
        return True
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, MUTEX)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        # Soltar el handle: si no, mantendría vivo el mutex aunque la otra ventana se
        # cierre, y esperar a que quede libre (tras actualizar) no acabaría nunca.
        kernel32.CloseHandle(handle)
        return False
    return True


def _wait_single_instance(timeout_s: float = MUTEX_WAIT_S) -> bool:
    deadline = time.monotonic() + timeout_s
    while not _single_instance():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)
    return True


def _focus_existing(path: str | None, home: Path) -> None:
    """Pide a la ventana que ya existe mostrarse (aunque esté en la bandeja) y abrir
    `path`. Se hace por archivo y no buscando la ventana por título: escondida no
    es visible, y la bandeja crea su propia ventana oculta."""
    import ctypes

    # ASFW_ANY: que la ventana existente pueda pasar al frente.
    ctypes.windll.user32.AllowSetForegroundWindow(-1)  # pyright: ignore[reportAttributeAccessIssue]
    (home / "app" / "open-path.txt").write_text(path or "", encoding="utf-8")


def background() -> str:
    """Fondo inicial según el tema de Windows (la app sigue el mismo)."""
    if os.name == "nt":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            if winreg.QueryValueEx(key, "AppsUseLightTheme")[0]:
                return LIGHT_BACKGROUND
        except OSError:
            pass
    return BACKGROUND


def _set_app_id() -> None:
    """Agrupa la ventana en la barra de tareas como "Oraqlo", no como Python."""
    if os.name == "nt":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def _set_icon(window, icon: Path) -> None:
    """WinForms no toma el icono de `webview.start`: se fija en el Form nativo."""
    try:
        import clr  # type: ignore[import-not-found]  # noqa: F401
        from System.Drawing import Icon  # type: ignore[import-not-found]

        window.native.Icon = Icon(str(icon))
    except Exception as e:
        log.debug("Sin icono de ventana: %s", e)


# ── app ───────────────────────────────────────────────────────────────────


class DesktopApp:
    def __init__(self, home: Path, path: str | None = None, hidden: bool = False) -> None:
        self.home = Path(home)
        self.path = path
        self.hidden = hidden  # escondida en la bandeja
        self.window: Any = None
        self.tray: Any = None
        self.quitting = False
        self.hint_shown = False
        self.geometry = load_geometry(self.home)

    @property
    def icon(self) -> Path:
        return self.home / "setup" / "oraqlo.ico"

    def ensure_server(self, timeout_s: float = 45.0) -> int:
        """Puerto donde responde Oraqlo; arranca la tarea programada si hace falta."""
        port = read_port(self.home)
        if healthy(port):
            return port
        import subprocess

        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            ["schtasks", "/Run", "/TN", "Oraqlo"], capture_output=True, creationflags=flags
        )
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            port = read_port(self.home)
            if healthy(port):
                return port
            time.sleep(1)
        raise RuntimeError("Oraqlo no respondió")

    # ventana

    def show(self) -> None:
        self.hidden = False
        if self.window is not None:
            self.window.show()
            self.window.restore()

    def on_resized(self, width: int, height: int) -> None:
        self.geometry.update(width=width, height=height)

    def on_moved(self, x: int, y: int) -> None:
        self.geometry.update(x=x, y=y)

    def on_closing(self) -> bool:
        """La X esconde la ventana en la bandeja (el servidor sigue)."""
        save_geometry(self.home, self.geometry)
        if self.quitting or self.tray is None:
            return True
        # Este evento corre en el hilo de la ventana y hide() vuelve a él: llamarlo
        # aquí mismo lo bloquea. Se esconde desde otro hilo al terminar el evento.
        self.hidden = True
        threading.Thread(target=self.window.hide, daemon=True).start()
        if not self.hint_shown:
            self.hint_shown = True
            self.tray.notify("Oraqlo sigue en la bandeja. Ábrelo desde aquí.", TITLE)
        return False

    def watch_open_requests(self) -> None:
        """Otra instancia pidió abrir una ruta (p. ej. desde un aviso)."""
        request = self.home / "app" / "open-path.txt"
        while not self.quitting:
            if request.exists():
                try:
                    path = request.read_text(encoding="utf-8").strip()
                    request.unlink(missing_ok=True)
                    port = read_port(self.home)
                    if path:
                        self.window.load_url(app_url(port, path))
                    else:
                        # Abrir Oraqlo otra vez también trae la interfaz al día: tras
                        # actualizar, la página guardada en caché sería la vieja.
                        self.window.load_url(reload_url(self.window.get_current_url(), port))
                    self.show()
                except Exception as e:
                    log.debug("open-path: %s", e)
            time.sleep(0.5)

    def check_version(self, spawn=subprocess.Popen) -> bool:
        """Si el servidor ya es otra versión, abre la ventana nueva y cierra esta.

        La ventana es un proceso aparte que no reinicia nadie: sin esto seguiría con
        el código (y la interfaz) de la versión con que se abrió, actualización tras
        actualización."""
        if not needs_restart(__version__, server_version(read_port(self.home))):
            return False
        cmd = relaunch_command(self.home, self.hidden)
        if cmd is None:
            return False
        log.info("El servidor ya es otra versión: se reabre la ventana (%s)", cmd[0])
        spawn(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.quit()
        return True

    def watch_version(self) -> None:
        while not self.quitting:
            time.sleep(VERSION_CHECK_S)
            try:
                if self.check_version():
                    return
            except Exception as e:
                log.debug("version: %s", e)

    def quit(self) -> None:
        self.quitting = True
        if self.tray is not None:
            self.tray.stop()
        if self.window is not None:
            self.window.destroy()

    # bandeja

    def start_tray(self) -> None:
        import pystray  # pyright: ignore[reportMissingImports]
        from PIL import Image  # pyright: ignore[reportMissingImports]

        def open_browser(_icon=None, _item=None):
            webbrowser.open(app_url(read_port(self.home)))

        def quit_app(_icon=None, _item=None):
            self.quit()

        image = Image.open(self.icon) if self.icon.exists() else Image.new("RGB", (64, 64))
        self.tray = pystray.Icon(
            "oraqlo-tray",  # nombre interno distinto del título de la ventana
            image,
            TITLE,
            menu=pystray.Menu(
                pystray.MenuItem("Abrir Oraqlo", lambda *_: self.show(), default=True),
                pystray.MenuItem("Abrir en el navegador", open_browser),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Cerrar la ventana", quit_app),
            ),
        )
        self.tray.run_detached()

    def run(self) -> int:
        import webview  # pyright: ignore[reportMissingImports]

        port = self.ensure_server()
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        self.window = webview.create_window(
            TITLE,
            app_url(port, self.path),
            min_size=MIN_SIZE,
            background_color=background(),
            text_select=True,
            hidden=self.hidden,
            **self.geometry,
        )
        self.window.events.closing += self.on_closing
        self.window.events.resized += self.on_resized
        self.window.events.moved += self.on_moved
        if self.icon.exists():
            # before_show corre en el hilo de la ventana: fijar el icono desde otro hilo
            # (evento `shown`) junto con la bandeja dejaba la ventana colgada.
            self.window.events.before_show += lambda: _set_icon(self.window, self.icon)
        try:
            self.start_tray()
        except Exception as e:  # sin bandeja la ventana sigue siendo útil
            log.warning("Sin icono en la bandeja: %s", e)
            self.tray = None
        threading.Thread(target=self.watch_open_requests, daemon=True).start()
        threading.Thread(target=self.watch_version, daemon=True).start()
        webview.start(
            private_mode=False,  # recuerda sesión/ajustes del navegador interno
            storage_path=str(self.home / "app" / "webview"),
        )
        self.quitting = True
        if self.tray is not None:
            self.tray.stop()
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Oraqlo en su propia ventana.")
    parser.add_argument("--home", default=os.environ.get("ORAQLO_HOME"), required=False)
    parser.add_argument("--path", default=None)
    parser.add_argument("--hidden", action="store_true", help="empezar en la bandeja")
    parser.add_argument(
        "--after-update", action="store_true", help="relevo tras actualizar: esperar a la vieja"
    )
    args = parser.parse_args(argv)
    if not args.home:
        from oraqlo.config import default_home

        args.home = str(default_home())
    home = Path(args.home)
    (home / "app").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=home / "app" / "desktop.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    first = _wait_single_instance() if args.after_update else _single_instance()
    if not first:
        _focus_existing(args.path, home)
        return 0
    _set_app_id()
    try:
        return DesktopApp(home, args.path, hidden=args.hidden).run()
    except Exception:
        log.exception("La ventana de Oraqlo falló; se abrirá el navegador")
        return FALLBACK_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main())
