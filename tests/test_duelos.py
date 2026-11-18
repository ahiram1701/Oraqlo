"""Práctica, pregunta a pregunta: Oraqlo frente al mercado."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from oraqlo.autopractice import MARKET_SOURCE, duels, market_tag
from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.memory.calibration import CalibrationLedger
from test_api import env  # noqa: F401  (fixture compartida)


def _pair(ledger, mid, oraqlo, market, winner=None, oraqlo_source="llm:m"):
    for fid, source, outcomes in (
        (f"o{mid}", oraqlo_source, oraqlo),
        (f"m{mid}", MARKET_SOURCE, market),
    ):
        if outcomes is None:
            continue
        ledger.record(
            Forecast(
                id=fid,
                question=f"{market_tag(mid)} ¿Pregunta {mid}?",
                distribution=Distribution(outcomes=outcomes),
                horizon=timedelta(days=3),
                assumptions=[],
                source=source,
                created_at=datetime.now(UTC) - timedelta(days=5),
            )
        )
        if winner:
            ledger.resolve(fid, winner)


def test_duelos_ganados_perdidos_empates_y_abiertos():
    ledger = CalibrationLedger()
    _pair(
        ledger, "1", {"Yes": 0.9, "No": 0.1}, {"Yes": 0.6, "No": 0.4}, winner="Yes"
    )  # gana Oraqlo
    _pair(
        ledger, "2", {"Yes": 0.2, "No": 0.8}, {"Yes": 0.7, "No": 0.3}, winner="Yes"
    )  # gana el mercado
    _pair(ledger, "3", {"Yes": 0.5, "No": 0.5}, {"Yes": 0.5, "No": 0.5}, winner="No")  # empate
    _pair(ledger, "4", {"Yes": 0.3, "No": 0.7}, {"Yes": 0.4, "No": 0.6})  # abierto
    _pair(ledger, "5", {"Yes": 0.3, "No": 0.7}, None)  # sin pareja: se ignora

    d = duels(ledger)
    s = d["summary"]
    assert (s["resolved"], s["open"], s["won"], s["lost"], s["tied"]) == (3, 1, 1, 1, 1)
    assert s["chance_brier"] == pytest.approx(0.5)
    by_id = {i["market_id"]: i for i in d["items"]}
    assert set(by_id) == {"1", "2", "3", "4"}
    assert by_id["1"]["winner"] == "oraqlo" and by_id["1"]["question"] == "¿Pregunta 1?"
    assert (by_id["2"]["oraqlo_p"], by_id["2"]["market_p"]) == (0.2, 0.7)  # prob. de lo que ganó
    # Abierto: se compara el «sí».
    assert by_id["4"]["status"] == "abierto" and by_id["4"]["label"] == "Yes"
    assert by_id["4"]["winner"] is None and by_id["4"]["closes_at"]


def test_mercado_con_nombres_compara_el_favorito_del_mercado():
    ledger = CalibrationLedger()
    _pair(ledger, "9", {"Ana": 0.4, "Bea": 0.6}, {"Ana": 0.7, "Bea": 0.3})
    item = duels(ledger)["items"][0]
    assert item["label"] == "Ana" and item["oraqlo_p"] == 0.4


def test_la_api_de_practica_incluye_los_duelos(env):
    client, service, *_ = env
    _pair(service.store.ledger, "1", {"Yes": 0.9, "No": 0.1}, {"Yes": 0.6, "No": 0.4}, winner="Yes")
    body = client.get("/api/practice").json()
    assert body["duels"]["summary"]["won"] == 1
    assert body["duels"]["items"][0]["question"] == "¿Pregunta 1?"


def test_calibracion_y_practica_cuentan_lo_mismo(env):
    """El mercado no entra en el boletín de Oraqlo y las preguntas que ya se sabían
    no cuentan en ninguna de las dos pantallas: las cifras tienen que cuadrar."""
    client, service, *_ = env
    ledger = service.store.ledger
    _pair(ledger, "1", {"Yes": 0.9, "No": 0.1}, {"Yes": 0.6, "No": 0.4}, winner="Yes")
    _pair(ledger, "2", {"Yes": 0.2, "No": 0.8}, {"Yes": 0.7, "No": 0.3}, winner="Yes")
    _pair(
        ledger, "3", {"Yes": 0.5, "No": 0.5}, {"Yes": 0.99, "No": 0.01}, winner="Yes"
    )  # ya se sabía

    duel = client.get("/api/practice").json()["duels"]["summary"]
    cal = client.get("/api/calibration?kind=practica").json()
    assert duel["resolved"] == cal["n"] == 2
    assert cal["summary"]["skill"] == pytest.approx(duel["oraqlo_skill"])
    assert duel["market_skill"] == pytest.approx(1 - duel["market_brier"] / duel["chance_brier"])
    # El mercado sale aparte, solo como referencia.
    ref = [s for s in cal["sources"] if s.get("reference")]
    assert [(s["source"], s["n"]) for s in ref] == [(MARKET_SOURCE, 2)]
    assert all(
        not s.get("reference")
        for s in client.get("/api/calibration?kind=prevision").json()["sources"]
    )
