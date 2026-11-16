"""Tras actualizar, el servidor nuevo reemplaza la ventana de escritorio vieja.

La ventana (`oraqlo.desktop`) es un proceso aparte que no reinicia nadie: la X solo
la esconde en la bandeja y reabrirla solo la muestra. Las ventanas de la 0.8 en
adelante vigilan la versión y se relanzan solas, pero una ventana más antigua no
tiene ese código y seguiría para siempre con su interfaz. El servidor sí se
reinicia en cada actualización, así que es él quien, al arrancar, cierra cualquier
ventana de otra versión y abre la nueva (escondida si la vieja lo estaba).

Solo Windows (instalación gestionada). Nunca lanza excepciones: lo peor que puede
pasar es que la ventana vieja siga ahí, como antes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"[\\/]versions[\\/]([^\\/\s\"]+)[\\/]", re.IGNORECASE)
# --home "C:\ruta con espacios" o --home C:\ruta
_HOME_RE = re.compile(r'--home\s+(?:"([^"]+)"|(\S+))')
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class DesktopWindow:
    """Una ventana de Oraqlo: el lanzador del venv (`root`) y sus procesos hijos."""

    root: int
    pids: tuple[int, ...]
    version: str


def _norm(path: str) -> str:
    return path.replace("/", "\\").rstrip("\\").lower()


def find_windows(rows: Iterable[dict], home: Path) -> list[DesktopWindow]:
    """Ventanas de este `home` a partir de la lista de procesos (ProcessId,
    ParentProcessId, CommandLine). En Windows el `pythonw.exe` del venv arranca el
    intérprete real como hijo con la misma línea de comandos: se agrupan."""
    home_n = _norm(str(home))
    found: dict[int, tuple[int, str]] = {}
    for row in rows:
        cmd = str(row.get("CommandLine") or "")
        home_arg = _HOME_RE.search(cmd)
        if "-m oraqlo.desktop" not in cmd or not home_arg:
            continue
        if _norm(home_arg.group(1) or home_arg.group(2)) != home_n:
            continue  # otra instalación (otro home)
        match = _VERSION_RE.search(cmd)
        if not match:
            continue  # sin carpeta de versión (p. ej. desde el código fuente)
        try:
            found[int(row["ProcessId"])] = (int(row.get("ParentProcessId") or 0), match.group(1))
        except (KeyError, TypeError, ValueError):
            continue
    windows = []
    for pid, (parent, version) in found.items():
        if parent in found:
            continue  # hijo de otra ventana de la lista
        children = tuple(p for p, (par, _v) in found.items() if par == pid)
        windows.append(DesktopWindow(root=pid, pids=(pid, *children), version=version))
    return windows


def list_processes() -> list[dict]:
    """Procesos de Python con su línea de comandos (tasklist no la da)."""
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
        "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=_NO_WINDOW,
    ).stdout.strip()
    if not out:
        return []
    data = json.loads(out)
    return data if isinstance(data, list) else [data]


def any_visible(pids: Iterable[int]) -> bool:
    """¿Alguna ventana de nivel superior visible de estos procesos? (la bandeja no cuenta)."""
    import ctypes
    from ctypes import wintypes

    wanted = set(pids)
    user32 = ctypes.windll.user32  # pyright: ignore[reportAttributeAccessIssue]
    seen = [False]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)  # pyright: ignore[reportAttributeAccessIssue]
    def callback(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in wanted and user32.IsWindowVisible(hwnd):
            seen[0] = True
            return False  # basta con una
        return True

    user32.EnumWindows(callback, 0)
    return seen[0]


def kill_tree(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        timeout=30,
        creationflags=_NO_WINDOW,
    )


def spawn(cmd: list[str]) -> None:
    subprocess.Popen(cmd, creationflags=_NO_WINDOW)


def replace_old_windows(
    home: Path,
    current: str,
    processes: Callable[[], list[dict]] = list_processes,
    visible: Callable[[Iterable[int]], bool] = any_visible,
    kill: Callable[[int], None] = kill_tree,
    launch: Callable[[list[str]], None] = spawn,
    note: Callable[[str], None] = log.info,
) -> list[str]:
    """Cierra las ventanas de otra versión y abre la de la versión activa.
    Devuelve las versiones reemplazadas (vacío si no había nada que hacer)."""
    try:
        old = [w for w in find_windows(processes(), home) if w.version != current]
        if not old:
            return []
        shown = any(visible(w.pids) for w in old)
        for w in old:
            kill(w.root)

        from oraqlo.launcher import Launcher, desktop_command

        cmd = desktop_command(Launcher(home))
        if cmd is not None:
            # --after-update: espera a que se libere el mutex de la ventana cerrada.
            launch([*cmd, "--after-update"] + ([] if shown else ["--hidden"]))
        versions = sorted({w.version for w in old})
        note(
            f"ventana {', '.join(versions)} reemplazada por {current}"
            + ("" if shown else " (en la bandeja)")
        )
        return versions
    except Exception as e:  # nunca tumbar el servidor por esto
        log.warning("No se pudo reemplazar la ventana vieja: %s", e)
        return []


def supported() -> bool:
    return os.name == "nt"
