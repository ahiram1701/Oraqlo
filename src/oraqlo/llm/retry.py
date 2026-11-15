"""Reintentos con backoff exponencial + jitter para errores transitorios de red.

Los errores transitorios (timeouts, 429, 5xx) se reintentan; los permanentes
(401/403, petición inválida) fallan a la primera con su mensaje accionable.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable

log = logging.getLogger(__name__)


class TransientError(Exception):
    """Envuelve un fallo que merece reintento (el adapter decide qué lo es)."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def retry_call[T](
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 20.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Ejecuta `fn`; si lanza TransientError reintenta hasta `attempts` veces en total.

    Tras agotar los intentos relanza la causa original (`__cause__`) si existe,
    para que quien llama vea el error de dominio y no el envoltorio.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except TransientError as e:
            if attempt == attempts:
                if e.__cause__ is not None:
                    raise e.__cause__ from None
                raise
            delay = e.retry_after or min(max_delay, base_delay * 2 ** (attempt - 1))
            delay *= 1 + random.uniform(0, 0.25)
            log.warning(
                "Error transitorio (%s); reintento %d/%d en %.1fs", e, attempt, attempts - 1, delay
            )
            sleep(delay)
    raise AssertionError("inalcanzable")  # pragma: no cover


def is_transient_status(status: int) -> bool:
    return status in (408, 409, 425, 429) or status >= 500
