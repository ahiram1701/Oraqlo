"""Avisos nativos de Windows (toasts) emitidos por el propio servidor.

El servidor corre en la sesión del usuario (tarea programada interactiva), así
que puede mostrar toasts aunque la ventana de Oraqlo esté cerrada. Al pulsar un
aviso se abre Oraqlo en la pantalla correspondiente (`launcher.py --open --path`).

Solo actúa en Windows y en una instalación gestionada; en Linux, CI o una copia
de desarrollo no hace nada.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

SETTING = "notify.windows"


class DesktopNotifier:
    def __init__(self, home: Path, enabled: bool = True) -> None:
        self.home = Path(home)
        self.available = enabled and os.name == "nt"
        self._toaster = None

    @property
    def icon(self) -> Path:
        return self.home / "setup" / "oraqlo.ico"

    def _open(self, url: str) -> None:
        launcher = self.home / "app" / "launcher.py"
        pythonw = (self.home / "app" / "pythonw.txt").read_text(encoding="utf-8-sig").strip()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            [pythonw, str(launcher), "--open", "--home", str(self.home), "--path", url],
            creationflags=flags,
        )

    def send(self, title: str, body: str, url: str = "/") -> bool:
        """Muestra un toast. True si se mostró."""
        if not self.available:
            return False
        try:
            from windows_toasts import (  # pyright: ignore[reportMissingImports]
                Toast,
                ToastDisplayImage,
                WindowsToaster,
            )

            if self._toaster is None:
                self._toaster = WindowsToaster("Oraqlo")
            toast = Toast([title, body])
            if self.icon.exists():
                toast.AddImage(ToastDisplayImage.fromPath(str(self.icon)))
            toast.on_activated = lambda _args: self._open(url)
            self._toaster.show_toast(toast)
            return True
        except Exception as e:  # un aviso nunca debe tumbar nada
            log.warning("No pude mostrar el aviso de Windows: %s", e)
            return False


def supported(managed: bool) -> bool:
    # ORAQLO_DESKTOP_NOTIFY=0 los apaga (los tests nunca muestran toasts reales).
    enabled = os.environ.get("ORAQLO_DESKTOP_NOTIFY", "1") != "0"
    return enabled and managed and sys.platform == "win32"
