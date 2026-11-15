"""Evita que Windows suspenda el PC mientras Oraqlo trabaja.

Con un análisis en curso, si el PC se duerme el celular pierde el servidor.
`SetThreadExecutionState` es por hilo, y los jobs empiezan y terminan en hilos
distintos; por eso un único hilo dedicado reinicia el temporizador de
inactividad del sistema cada 30 s mientras haya trabajo activo (sin
ES_CONTINUOUS: si el proceso muere, el efecto desaparece solo).
Fuera de Windows no hace nada.
"""

from __future__ import annotations

import os
import threading

ES_SYSTEM_REQUIRED = 0x00000001


class KeepAwake:
    def __init__(self, period_s: float = 30.0) -> None:
        self._count = 0
        self._lock = threading.Lock()
        self._period_s = period_s
        self._thread: threading.Thread | None = None

    def _ping(self) -> None:
        if os.name == "nt":
            import ctypes

            ctypes.windll.kernel32.SetThreadExecutionState(ES_SYSTEM_REQUIRED)  # type: ignore[attr-defined]

    def _loop(self) -> None:
        while True:
            with self._lock:
                if self._count == 0:
                    self._thread = None
                    return
            self._ping()
            threading.Event().wait(self._period_s)

    def acquire(self) -> None:
        with self._lock:
            self._count += 1
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._loop, name="oraqlo-keepawake", daemon=True
                )
                self._thread.start()

    def release(self) -> None:
        with self._lock:
            self._count = max(0, self._count - 1)

    @property
    def active(self) -> bool:
        return self._count > 0


keep_awake = KeepAwake()
