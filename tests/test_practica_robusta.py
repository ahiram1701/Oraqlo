"""Práctica robusta: un fallo aislado no se lleva por delante la pasada entera."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from oraqlo import autopractice
from oraqlo.autopractice import (
    MARKET_SOURCE,
    PracticeEngine,
    PracticeError,
    market_tag,
    practice_source,
)
from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.memory.calibration import CalibrationLedger
from oraqlo.polymarket import Market, PolymarketClient, _parse_market
from oraqlo.store.db import Database
from oraqlo.store.ledger import SqliteLedger


def _market(mid: str) -> Market:
    return Market(
        id=mid,
        question=f"¿{mid}?",
        outcomes={"Yes": 0.5, "No": 0.5},
        end_date=datetime.now(UTC) + timedelta(days=5),
        closed=False,
        resolved=False,
        winner=None,
        volume_24h=50_000.0,
        raw={"slug": f"evento-{mid}"},
    )


def _forecast(mid: str, source: str = "llm:m", fid: str | None = None, age: float = 2) -> Forecast:
    return Forecast(
        id=fid or f"{mid}-{source}",
        question=f"{market_tag(mid)} ¿{mid}?",
        distribution=Distribution(outcomes={"Yes": 0.6, "No": 0.4}),
        horizon=timedelta(days=1),
        assumptions=[],
        source=source,
        created_at=datetime.now(UTC) - timedelta(days=age),
    )


class OpenClient:
    def __init__(self, markets):
        self.markets = markets

    def list_open(self, limit=20, **_):
        return list(self.markets)

    def get_markets(self, ids, closed):
        return {}


def test_un_mercado_que_falla_no_anula_los_demas(monkeypatch):
    ledger = CalibrationLedger()
    client = OpenClient([_market("1"), _market("2")])
    engine = PracticeEngine(client, None, "m", ledger, markets_per_run=2)  # type: ignore[arg-type]

    def forecast(market, *a, **k):
        if market.id == "1":
            raise PracticeError("el modelo no respondió")
        return _forecast(market.id, fid="ok"), []

    monkeypatch.setattr(engine, "_forecast_market", forecast)
    report = engine.run_once(research=False)
    assert [m.id for m, _ in report.predicted] == ["2"]
    assert any("el modelo no respondió" in e for e in report.errors)


def test_una_resolucion_que_falla_no_corta_las_demas(monkeypatch):
    ledger = CalibrationLedger()
    for mid in ("1", "2"):
        ledger.record(_forecast(mid))
    winner = {"Yes": 1.0, "No": 0.0}

    class Resolved(OpenClient):
        def get_markets(self, ids, closed):
            return {
                m: Market(m, "¿?", winner, None, True, True, "Yes", raw={}) for m in ids if closed
            }

    engine = PracticeEngine(Resolved([]), None, "m", ledger)  # type: ignore[arg-type]
    real = ledger.resolve

    def resolve(fid, outcome):
        if fid.startswith("1-"):
            raise KeyError(fid)
        return real(fid, outcome)

    monkeypatch.setattr(ledger, "resolve", resolve)
    resolved = engine.resolve_due()
    assert [r.forecast.id for r in resolved] == ["2-llm:m"]
    assert engine.errors and "1-llm:m" in engine.errors[0]


def test_sqlite_ledger_no_diverge_si_falla_la_base(tmp_path, monkeypatch):
    ledger = SqliteLedger(Database(tmp_path / "o.db"))
    ledger.record(_forecast("1"))

    class Broken:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("disco lleno")

    import contextlib

    @contextlib.contextmanager
    def tx():
        yield Broken()

    monkeypatch.setattr(ledger.db, "tx", tx)
    with pytest.raises(sqlite3.OperationalError):
        ledger.resolve("1-llm:m", "Yes")
    # La memoria sigue como el disco: abierta y sin resolver.
    assert [f.id for f in ledger.open_forecasts] == ["1-llm:m"]
    assert ledger.resolved_forecasts == []


def test_mercados_malformados_se_saltan_en_list_open(monkeypatch):
    good = {
        "id": "1",
        "question": "¿Bien?",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.4", "0.6"]',
        "endDate": (datetime.now(UTC) + timedelta(days=5)).isoformat(),
        "volume24hr": 50_000,
    }
    uneven = {**good, "id": "2", "outcomePrices": '["0.4"]'}
    bad_volume = {**good, "id": "3", "volume24hr": "mucho"}
    client = PolymarketClient()
    pages = [[good, uneven, bad_volume], []]
    monkeypatch.setattr(client, "_get", lambda path, params=None: pages.pop(0) if pages else [])
    assert [m.id for m in client.list_open(limit=5)] == ["1"]


def test_parse_market_rechaza_longitudes_distintas():
    from oraqlo.polymarket import PolymarketError

    with pytest.raises(PolymarketError):
        _parse_market({"id": "1", "outcomes": '["A", "B", "C"]', "outcomePrices": '["1", "0"]'})


def test_fuente_de_practica_es_la_del_comite_si_lo_hay():
    ledger = CalibrationLedger()
    assert practice_source(ledger, "llm:m") == "llm:m"
    ledger.record(_forecast("1", "llm:m", age=5))
    ledger.record(_forecast("2", "llm:comite", age=1))
    ledger.record(_forecast("2", MARKET_SOURCE, age=0))
    assert practice_source(ledger, "llm:m") == "llm:comite"


def test_cli_rechaza_intervalo_no_positivo():
    with pytest.raises(SystemExit):
        autopractice.main(["--interval-min", "0"])


def test_bucle_cli_sobrevive_a_una_pasada_fallida(monkeypatch, capsys):
    from oraqlo.services import core

    calls = []

    class FakeService:
        def __init__(self):
            self.config = type("C", (), {"practice": type("P", (), {"markets": 2})()})()

        def practice(self, research, emit):
            calls.append(1)
            if len(calls) == 1:
                raise core.PracticeFailed("Polymarket caído")
            raise KeyboardInterrupt  # segunda pasada: salimos del bucle

    monkeypatch.setattr(core, "OraqloService", FakeService)
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(KeyboardInterrupt):
        autopractice.main(["--loop", "--interval-min", "1"])
    assert len(calls) == 2
    assert "Polymarket caído" in capsys.readouterr().out
