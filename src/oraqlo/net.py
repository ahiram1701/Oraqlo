"""GET de JSON con reintentos: la base común de los clientes HTTP de solo lectura.

Reintenta lo que suele arreglarse solo (HTTP 429/5xx y timeouts) con espera exponencial,
respetando un `Retry-After` corto. Lo demás (404, DNS caído, conexión rechazada) se
propaga al momento: el llamador traduce cada error a su propio tipo y mensaje.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER_S = 10.0  # un Retry-After más largo no se espera: se falla y ya


def _retry_after(error: urllib.error.HTTPError) -> float | None:
    value = error.headers.get("Retry-After") if error.headers else None
    try:
        return float(value) if value is not None else None
    except ValueError:  # también puede ser una fecha HTTP: se ignora
        return None


def get_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout_s: float = 20.0,
    retries: int = 2,
    backoff_s: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
):
    """El JSON de `url`. Lanza `urllib.error.HTTPError`, `URLError`, `TimeoutError`,
    `OSError` o `json.JSONDecodeError` tras agotar los reintentos."""
    request = urllib.request.Request(url, headers=headers or {})
    for attempt in range(retries + 1):
        last = attempt == retries
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if last or e.code not in RETRY_STATUS:
                raise
            wait = _retry_after(e)
            if wait is not None and wait > MAX_RETRY_AFTER_S:
                raise
            sleep(wait if wait is not None else backoff_s * 2**attempt)
        except (TimeoutError, urllib.error.URLError) as e:
            # Un timeout al conectar llega envuelto en URLError; el resto de URLError
            # (DNS, conexión rechazada) es falta de red y reintentar solo retrasa el aviso.
            timed_out = isinstance(e, TimeoutError) or isinstance(
                getattr(e, "reason", None), TimeoutError
            )
            if last or not timed_out:
                raise
            sleep(backoff_s * 2**attempt)
    raise AssertionError("inalcanzable")  # pragma: no cover
