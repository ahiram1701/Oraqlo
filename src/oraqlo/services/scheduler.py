"""Planificador de tareas periódicas del servidor (autopráctica, avisos, updates).

Un hilo que cada `tick_s` revisa las tareas registradas. La última ejecución de
cada tarea se guarda en la base, así un reinicio no dispara todo de golpe ni
olvida cuándo tocaba la siguiente. Las tareas no solapan consigo mismas.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from oraqlo.store.settings import SettingsStore

log = logging.getLogger(__name__)

RETRY_AFTER = timedelta(minutes=15)  # tras un fallo no se espera el intervalo completo


@dataclass
class PeriodicTask:
    name: str
    interval: Callable[[], timedelta]  # se evalúa en cada tick (la config puede cambiar)
    enabled: Callable[[], bool]
    action: Callable[[], None]
    run_on_start: bool = False  # si nunca corrió, ¿corre ya o espera un intervalo?


class Scheduler:
    def __init__(self, settings: SettingsStore, tick_s: float = 30.0) -> None:
        self.settings = settings
        self.tick_s = tick_s
        self.tasks: dict[str, PeriodicTask] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running: set[str] = set()

    def add(self, task: PeriodicTask) -> None:
        self.tasks[task.name] = task

    def _key(self, name: str) -> str:
        return f"scheduler.last_run.{name}"

    def _error_key(self, name: str) -> str:
        return f"scheduler.error.{name}"

    def errors(self) -> dict[str, dict]:
        """Último fallo de cada tarea que no se ha recuperado ({nombre: {at, error}})."""
        found = {}
        for name in self.tasks:
            value = self.settings.get(self._error_key(name))
            if value:
                found[name] = value
        return found

    def last_run(self, name: str) -> datetime | None:
        value = self.settings.get(self._key(name))
        return datetime.fromisoformat(value) if value else None

    def due(self, task: PeriodicTask, now: datetime) -> bool:
        if not task.enabled() or task.name in self._running:
            return False
        last = self.last_run(task.name)
        if last is None:
            if task.run_on_start:
                return True
            # Primera vez: arma el temporizador sin ejecutar (abrir la app no cuesta llamadas).
            self.settings.set(self._key(task.name), now.isoformat())
            return False
        return now - last >= task.interval()

    def tick(self, now: datetime | None = None) -> list[str]:
        """Ejecuta las tareas vencidas. Devuelve sus nombres (útil en tests)."""
        now = now or datetime.now(UTC)
        ran = []
        for task in list(self.tasks.values()):
            try:
                if not self.due(task, now):
                    continue
                self._running.add(task.name)
                # Se marca antes de ejecutar: una tarea lenta no se lanza dos veces.
                self.settings.set(self._key(task.name), now.isoformat())
                task.action()
                ran.append(task.name)
                if self.settings.get(self._error_key(task.name)):
                    self.settings.set(self._error_key(task.name), None)
            except Exception as e:
                log.exception("La tarea periódica %s falló", task.name)
                # Reintento en RETRY_AFTER (no un día entero si es el backup diario)
                # y el error queda guardado para mostrarlo en Diagnóstico.
                interval = task.interval()
                retry_at = now - interval + min(RETRY_AFTER, interval)
                self.settings.set(self._key(task.name), retry_at.isoformat())
                self.settings.set(
                    self._error_key(task.name), {"at": now.isoformat(), "error": str(e)[:300]}
                )
            finally:
                self._running.discard(task.name)
        return ran

    def start(self) -> None:
        if self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.wait(self.tick_s):
                self.tick()

        self._thread = threading.Thread(target=loop, name="oraqlo-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
