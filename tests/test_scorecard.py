"""Calibración que se entiende: frente al azar, por previsión, tendencia y filtros."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.memory.calibration import CalibrationLedger
from oraqlo.memory.scorecard import chance_brier, forecast_kind, scorecard, select
from test_api import env  # noqa: F401  (fixture compartida)

NOW = datetime(2026, 11, 16, tzinfo=UTC)


def _resolve(
    ledger: CalibrationLedger,
    fid: str,
    outcomes: dict[str, float],
    actual: str,
    source: str = "llm:m:prevision",
    horizon: float = 30,
    resolved_at: datetime = NOW,
):
    ledger.record(
        Forecast(
            id=fid,
            question=f"[polymarket:{fid}] ¿{fid}?" if source == "llm:m" else f"¿{fid}?",
            distribution=Distribution(outcomes=outcomes),
            horizon=timedelta(days=horizon),
            assumptions=[],
            source=source,
            created_at=resolved_at - timedelta(days=horizon),
        )
    )
    r = ledger.resolve(fid, actual)
    r.resolved_at = resolved_at  # fecha controlada para la tendencia
    return r


def test_tipos_de_fuente():
    assert forecast_kind("llm:qwen3:8b:panel") == "panel"
    assert forecast_kind("llm:m:objetivo") == "objetivo"
    assert forecast_kind("llm:m", "[polymarket:7] ¿X?") == "practica"
    assert forecast_kind("polymarket:mercado") == "practica"


def test_azar_y_habilidad_con_datos_conocidos():
    ledger = CalibrationLedger()
    # Binaria: el azar (50/50) saca 0.5 en Brier multiclase.
    a = _resolve(ledger, "a", {"sí": 0.9, "no": 0.1}, "sí")
    assert chance_brier(a) == pytest.approx(0.5)
    # Tres escenarios: el azar saca 1 − 1/3.
    b = _resolve(ledger, "b", {"x": 0.6, "y": 0.3, "z": 0.1}, "y")
    assert chance_brier(b) == pytest.approx(2 / 3)

    card = scorecard(ledger.resolved_forecasts)
    s = card["summary"]
    assert s["n"] == 2
    assert s["brier"] == pytest.approx((a.brier + b.brier) / 2)
    assert s["chance"] == pytest.approx((0.5 + 2 / 3) / 2)
    assert s["skill"] == pytest.approx(1 - s["brier"] / s["chance"])
    # Acertó lo más probable en 1 de 2, con una confianza media de 0.75.
    assert s["hit_rate"] == pytest.approx(0.5)
    assert s["confidence"] == pytest.approx(0.75)
    assert s["overconfidence"] == pytest.approx(0.25)


def test_fiabilidad_un_punto_por_prevision():
    ledger = CalibrationLedger()
    for i in range(4):
        _resolve(ledger, f"p{i}", {"sí": 0.8, "no": 0.2}, "sí" if i < 3 else "no")
    rel = scorecard(ledger.resolved_forecasts)["reliability"]
    filled = [b for b in rel if b["n"]]
    assert sum(b["n"] for b in rel) == 4  # no 8 (una por etiqueta)
    assert filled[0]["confidence"] == pytest.approx(0.8)
    assert filled[0]["observed"] == pytest.approx(0.75)


def test_tendencia_desgloses_peores_y_filtros():
    ledger = CalibrationLedger()
    _resolve(ledger, "oct", {"sí": 0.7, "no": 0.3}, "sí", resolved_at=NOW - timedelta(days=40))
    _resolve(ledger, "nov", {"sí": 0.95, "no": 0.05}, "no", source="llm:otro:panel", horizon=300)
    _resolve(ledger, "prac", {"sí": 0.6, "no": 0.4}, "sí", source="llm:m")
    items = ledger.resolved_forecasts
    card = scorecard(items)

    assert [t["month"] for t in card["trend"]] == ["2026-10", "2026-11"]
    assert {k["kind"]: k["n"] for k in card["by_kind"]} == {
        "prevision": 1,
        "panel": 1,
        "practica": 1,
    }
    assert {h["horizon"] for h in card["by_horizon"]} == {"corto", "largo"}
    worst = card["worst"][0]
    assert worst["id"] == "nov" and worst["predicted"] == "sí" and worst["actual"] == "no"
    assert worst["actual_p"] == pytest.approx(0.05)

    assert [r.forecast.id for r in select(items, kind="panel")] == ["nov"]
    assert [r.forecast.id for r in select(items, model="m")] == ["oct", "prac"]
    assert [r.forecast.id for r in select(items, days=30, now=NOW)] == ["nov", "prac"]


def test_api_de_calibracion_con_filtros(env):
    client, service, *_ = env
    _resolve(service.store.ledger, "x", {"sí": 0.8, "no": 0.2}, "sí", resolved_at=datetime.now(UTC))
    _resolve(
        service.store.ledger,
        "y",
        {"sí": 0.8, "no": 0.2},
        "no",
        source="llm:m:panel",
        resolved_at=datetime.now(UTC),
    )
    body = client.get("/api/calibration").json()
    assert body["n"] == 2 and body["summary"]["skill"] is not None
    assert body["filters"] == {"kinds": ["prevision", "panel"], "models": ["m"]}
    assert client.get("/api/calibration?kind=panel").json()["n"] == 1
    assert client.get("/api/calibration?kind=nada").status_code == 422
    assert client.get("/api/calibration?days=1").json()["n"] == 2


def test_previsiones_antiguas_sin_tipo_no_son_practica():
    from oraqlo.memory.recalibration import user_resolvable

    # Antes de la 0.7 todo era `llm:<modelo>`: sin etiqueta de mercado, es una previsión.
    assert forecast_kind("llm:qwen3:8b", "¿Lloverá?") == "prevision"
    assert forecast_kind("llm:deepseek:cloud", "[polymarket:123] ¿Ganará X?") == "practica"
    assert user_resolvable("llm:m", "¿Lloverá?")
    assert not user_resolvable("llm:m", "[polymarket:9] ¿Algo?")
    assert not user_resolvable("polymarket:mercado", "[polymarket:9] ¿Algo?")
