"""Red común: reintentos con espera, list_open que excluye y caché de /api/practice."""

from __future__ import annotations

import io
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

import pytest

from oraqlo import net
from oraqlo.polymarket import PolymarketClient, PolymarketError
from test_api import env  # noqa: F401  (fixture compartida)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError("http://x", code, "err", headers, None)  # type: ignore[arg-type]


def _urlopen(outcomes):
    calls = []

    def fake(request, timeout):
        calls.append(request.full_url)
        item = outcomes.pop(0)
        if isinstance(item, BaseException):
            raise item
        return _Resp(item)

    return fake, calls


def test_reintenta_503_y_luego_responde(monkeypatch):
    fake, calls = _urlopen([_http(503), b'{"ok": 1}'])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    waits = []
    assert net.get_json("http://x", sleep=waits.append) == {"ok": 1}
    assert len(calls) == 2 and waits == [1.0]


def test_respeta_retry_after_corto_y_falla_con_uno_largo(monkeypatch):
    fake, _ = _urlopen([_http(429, "3"), b"[]"])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    waits = []
    assert net.get_json("http://x", sleep=waits.append) == []
    assert waits == [3.0]

    fake, calls = _urlopen([_http(429, "120")])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(urllib.error.HTTPError):
        net.get_json("http://x", sleep=waits.append)
    assert len(calls) == 1


def test_no_reintenta_404_ni_dns(monkeypatch):
    for error in (_http(404), urllib.error.URLError("getaddrinfo failed")):
        fake, calls = _urlopen([error])
        monkeypatch.setattr(urllib.request, "urlopen", fake)
        with pytest.raises(urllib.error.URLError):
            net.get_json("http://x", sleep=lambda s: None)
        assert len(calls) == 1


def test_reintenta_timeouts_y_agota(monkeypatch):
    fake, calls = _urlopen([TimeoutError(), urllib.error.URLError(TimeoutError()), TimeoutError()])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    waits = []
    with pytest.raises(TimeoutError):
        net.get_json("http://x", retries=2, sleep=waits.append)
    assert len(calls) == 3 and waits == [1.0, 2.0]


def test_polymarket_traduce_el_error_tras_reintentar(monkeypatch):
    fake, calls = _urlopen([_http(502), _http(502), _http(502)])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setattr(net.time, "sleep", lambda s: None)
    with pytest.raises(PolymarketError) as e:
        PolymarketClient().get_market("1")
    assert "HTTP 502" in str(e.value) and not e.value.offline and len(calls) == 3


def test_list_open_no_cuenta_los_excluidos(monkeypatch):
    end = (datetime.now(UTC) + timedelta(days=5)).isoformat()

    def item(mid):
        return {
            "id": mid,
            "question": f"¿{mid}?",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.4", "0.6"]',
            "endDate": end,
            "volume24hr": 50_000,
        }

    client = PolymarketClient()
    monkeypatch.setattr(client, "_get", lambda p, params=None: [item(str(i)) for i in range(5)])
    found = client.list_open(limit=2, exclude={"0", "1"}, max_pages=1)
    assert [m.id for m in found] == ["2", "3"]


def test_api_practica_cachea_hasta_que_cambia_el_ledger(env, monkeypatch):  # noqa: F811
    from oraqlo import autopractice

    client, service, *_ = env
    calls = []
    real = autopractice.duels
    monkeypatch.setattr(autopractice, "duels", lambda ledger: calls.append(1) or real(ledger))
    client.get("/api/practice")
    client.get("/api/practice")
    assert len(calls) == 1
    service.store.ledger.version += 1  # cualquier cambio en el ledger
    client.get("/api/practice")
    assert len(calls) == 2
