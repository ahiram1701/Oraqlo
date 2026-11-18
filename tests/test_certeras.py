"""Previsiones más certeras: sin mercados ya decididos, duelos justos y prudencia sin datos."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from oraqlo.autopractice import MARKET_SOURCE, PracticeEngine, duels, market_tag
from oraqlo.forecaster.aggregate import NO_DATA_NOTE, temper_without_data
from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.forecaster.llm import LLMForecaster
from oraqlo.llm.base import LLMProvider, LLMResponse
from oraqlo.memory.calibration import CalibrationLedger
from oraqlo.polymarket import Market, already_decided
from oraqlo.world_model import Observation, WorldModel
from test_api import env  # noqa: F401  (fixture compartida)

NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)


def _market(yes=0.4, **raw) -> Market:
    return Market(
        id="1",
        question="¿X?",
        outcomes={"Yes": yes, "No": 1 - yes},
        end_date=NOW + timedelta(days=5),
        closed=False,
        resolved=False,
        raw=raw,
    )


def test_no_se_eligen_mercados_ya_decididos():
    assert not already_decided(_market(0.4), NOW)
    assert already_decided(_market(1.0), NOW)  # partido ya jugado: precio 100/0
    assert already_decided(_market(0.02), NOW)  # el «No» al 98%
    assert already_decided(_market(0.5, acceptingOrders=False), NOW)
    assert already_decided(_market(0.5, gameStartTime="2026-11-17 10:00:00+00"), NOW)
    assert not already_decided(_market(0.5, gameStartTime="2026-11-18T10:00:00Z"), NOW)
    assert not already_decided(_market(0.5, gameStartTime="no es fecha"), NOW)


def _pair(ledger, mid, oraqlo, market, winner):
    for fid, source, outcomes in ((f"o{mid}", "llm:m", oraqlo), (f"m{mid}", MARKET_SOURCE, market)):
        ledger.record(
            Forecast(
                id=fid,
                question=f"{market_tag(mid)} ¿{mid}?",
                distribution=Distribution(outcomes=outcomes),
                horizon=timedelta(days=2),
                assumptions=[],
                source=source,
                created_at=NOW - timedelta(days=3),
            )
        )
        ledger.resolve(fid, winner)


def test_los_duelos_ya_decididos_no_cuentan():
    ledger = CalibrationLedger()
    _pair(ledger, "1", {"A": 0.7, "B": 0.3}, {"A": 1.0, "B": 0.0}, "A")  # el mercado ya lo sabía
    _pair(ledger, "2", {"A": 0.7, "B": 0.3}, {"A": 0.5, "B": 0.5}, "A")  # duelo de verdad
    d = duels(ledger)
    by_id = {i["market_id"]: i for i in d["items"]}
    assert by_id["1"]["already_decided"] and by_id["1"]["winner"] is None
    assert by_id["2"]["winner"] == "oraqlo"
    s = d["summary"]
    assert (s["resolved"], s["won"], s["lost"], s["already_decided"]) == (1, 1, 0, 1)


class _Fake(LLMProvider):
    def __init__(self, reply: dict):
        self.reply = json.dumps(reply)

    def chat(self, messages, model, temperature=0.7, json_mode=False, **kwargs):
        return LLMResponse(content=self.reply, model=model)


_REPLY = {"outcomes": {"sí": 0.7, "no": 0.3}, "assumptions": ["a"], "rationale": "r"}


def test_sin_datos_se_suaviza_con_datos_no():
    oracle = LLMForecaster(_Fake(_REPLY), "m", samples=1)
    blind = oracle.forecast(WorldModel().current(), "¿X?", timedelta(days=30))
    assert blind.distribution.outcomes == pytest.approx(temper_without_data({"sí": 0.7, "no": 0.3}))
    assert NO_DATA_NOTE in blind.assumptions

    world = WorldModel()
    world.apply(Observation(entity_id="x", variable="dato", value=1, source="web", timestamp=NOW))
    informed = oracle.forecast(world.current(), "¿X?", timedelta(days=30))
    assert informed.distribution.outcomes == pytest.approx({"sí": 0.7, "no": 0.3})
    assert NO_DATA_NOTE not in informed.assumptions


def test_practica_sin_investigar_tambien_es_prudente():
    engine = PracticeEngine(
        None, _Fake({"outcomes": {"Yes": 0.7, "No": 0.3}}), "m", CalibrationLedger()
    )  # type: ignore[arg-type]
    market = _market(0.4)
    blind, _ = engine._forecast_market(market, None, None)
    assert blind.distribution.outcomes["Yes"] == pytest.approx(
        temper_without_data({"Yes": 0.7, "No": 0.3})["Yes"]
    )
    informed, _ = engine._forecast_market(market, None, "Hechos: ranking 12 vs 300")
    assert informed.distribution.outcomes["Yes"] == pytest.approx(0.7)


def test_practica_dice_si_investiga(env, monkeypatch):
    client, *_ = env
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    assert client.get("/api/practice").json()["research"] is False
    monkeypatch.setenv("OLLAMA_API_KEY", "k")
    assert client.get("/api/practice").json()["research"] is True
