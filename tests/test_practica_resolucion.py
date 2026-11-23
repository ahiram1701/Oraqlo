"""Resolución de la Práctica: anulados, consulta por lotes, fase en UMA y sin conexión."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from oraqlo.autopractice import PracticeEngine
from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.memory.calibration import CalibrationLedger
from oraqlo.polymarket import Market, PolymarketClient, PolymarketError


def _forecast(mid: str, source: str = "llm:m", days_ago: float = 2, horizon: float = 1) -> Forecast:
    return Forecast(
        id=f"{mid}-{source}",
        question=f"[polymarket:{mid}] ¿X?",
        distribution=Distribution(outcomes={"Yes": 0.6, "No": 0.4}),
        horizon=timedelta(days=horizon),
        assumptions=[],
        source=source,
        created_at=datetime.now(UTC) - timedelta(days=days_ago),
    )


def _market(mid: str, prices=(0.6, 0.4), resolved=False, uma=None) -> Market:
    outcomes = {"Yes": prices[0], "No": prices[1]}
    winner = None
    if resolved and max(prices) >= 0.99:
        winner = "Yes" if prices[0] > prices[1] else "No"
    return Market(
        id=mid,
        question=f"¿{mid}?",
        outcomes=outcomes,
        end_date=None,
        closed=resolved,
        resolved=resolved,
        winner=winner,
        raw={"umaResolutionStatus": uma or ("resolved" if resolved else None)},
    )


class BatchClient:
    """Imita Gamma: con closed=True solo devuelve los cerrados y con False los abiertos."""

    def __init__(self, markets: dict[str, Market], fail: PolymarketError | None = None):
        self.markets, self.fail, self.calls = markets, fail, []

    def get_markets(self, ids, closed):
        self.calls.append((tuple(ids), closed))
        if self.fail:
            raise self.fail
        return {
            m: self.markets[m]
            for m in ids
            if m in self.markets and self.markets[m].closed == closed
        }

    def get_market(self, mid):
        self.calls.append((mid, "one"))
        return self.markets[mid]


def _engine(client, *forecasts) -> tuple[PracticeEngine, CalibrationLedger]:
    ledger = CalibrationLedger()
    for f in forecasts:
        ledger.record(f)
    return PracticeEngine(client, None, "m", ledger), ledger  # type: ignore[arg-type]


def test_mercado_anulado_50_50_se_descarta_y_no_queda_abierto():
    client = BatchClient({"1": _market("1", prices=(0.5, 0.5), resolved=True)})
    engine, ledger = _engine(client, _forecast("1"), _forecast("1", "polymarket:mercado"))
    assert engine.resolve_due() == []
    assert ledger.open_forecasts == [] and ledger.resolved_forecasts == []


def test_lotes_resuelven_los_cerrados_y_dan_la_fase_de_los_vencidos():
    client = BatchClient(
        {
            "1": _market("1", prices=(0.0, 1.0), resolved=True),
            "2": _market("2", uma="proposed"),
            "3": _market("3", uma="disputed"),
            "4": _market("4"),
        }
    )
    engine, _ = _engine(
        client,
        _forecast("1"),
        _forecast("2"),
        _forecast("3"),
        _forecast("4"),
        _forecast("5", days_ago=0, horizon=3),  # aún no vence: no se pregunta su fase
    )
    resolved = engine.resolve_due()
    assert [r.actual_outcome for r in resolved] == ["No"]
    assert engine.market_status == {"2": "proposed", "3": "disputed", "4": "pendiente"}
    # Dos peticiones (cerrados y fase de vencidos), no una por mercado.
    assert [closed for _, closed in client.calls] == [True, False]


def test_si_el_lote_falla_se_consulta_uno_a_uno():
    client = BatchClient(
        {"1": _market("1", prices=(1.0, 0.0), resolved=True)},
        fail=PolymarketError("HTTP 422"),
    )
    engine, _ = _engine(client, _forecast("1"))
    assert [r.actual_outcome for r in engine.resolve_due()] == ["Yes"]
    assert ("1", "one") in client.calls


def test_sin_conexion_no_es_un_fallo_de_la_pasada():
    client = BatchClient({}, fail=PolymarketError("getaddrinfo failed", offline=True))
    engine, _ = _engine(client, _forecast("1"))
    report = engine.run_once(research=False)
    assert report.offline and report.errors
    assert ("1", "one") not in client.calls  # sin red no se insiste mercado a mercado


def test_cliente_pide_lotes_con_id_repetido_y_closed(monkeypatch):
    client = PolymarketClient()
    seen = []

    def fake_get(path, params=None):
        seen.append((path, params))
        return [
            {
                "id": "9",
                "question": "¿?",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.5", "0.5"]',
                "closed": True,
                "umaResolutionStatus": "resolved",
            }
        ]

    monkeypatch.setattr(client, "_get", fake_get)
    found = client.get_markets(["9", "8", "9"], closed=True)
    assert found["9"].voided and found["9"].uma_status == "resolved"
    path, params = seen[0]
    assert path == "/markets" and ("id", "9") in params and ("id", "8") in params
    assert ("closed", "true") in params and len([p for p in params if p[0] == "id"]) == 2


def test_error_de_red_marca_offline(monkeypatch):
    import urllib.error
    import urllib.request

    def boom(*a, **k):
        raise urllib.error.URLError("getaddrinfo failed")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(PolymarketError) as e:
        PolymarketClient().get_market("1")
    assert e.value.offline
