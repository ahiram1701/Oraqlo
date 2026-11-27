"""SqliteLedger: lo que se escribe sobrevive a reabrir la base, y la versión avanza."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.forecaster.quant import holt_fit
from oraqlo.store.db import Database
from oraqlo.store.ledger import SqliteLedger


def _f(fid: str, source: str = "llm:m") -> Forecast:
    return Forecast(
        id=fid,
        question=f"¿{fid}?",
        distribution=Distribution(outcomes={"Sí": 0.7, "No": 0.3}),
        horizon=timedelta(days=3),
        assumptions=["a"],
        source=source,
        created_at=datetime.now(UTC),
    )


def test_persiste_abiertas_resueltas_descartes_y_borrados(tmp_path):
    path = tmp_path / "o.db"
    ledger = SqliteLedger(Database(path))
    for fid in ("a", "b", "c", "d"):
        ledger.record(_f(fid))
    ledger.resolve("a", "Sí")
    ledger.resolve("b", "No")
    ledger.discard("c")
    ledger.delete_resolved("b")

    again = SqliteLedger(Database(path))
    assert [f.id for f in again.open_forecasts] == ["d"]
    [r] = again.resolved_forecasts
    assert (r.forecast.id, r.actual_outcome) == ("a", "Sí")
    assert r.brier == pytest.approx(ledger.resolved_forecasts[0].brier)
    assert again.open_forecasts[0].assumptions == ["a"]


def test_volver_a_registrar_reabre_la_prevision(tmp_path):
    path = tmp_path / "o.db"
    ledger = SqliteLedger(Database(path))
    ledger.record(_f("a"))
    ledger.resolve("a", "Sí")
    ledger.record(_f("a"))  # ON CONFLICT: vuelve a abierta y sin Brier
    again = SqliteLedger(Database(path))
    assert [f.id for f in again.open_forecasts] == ["a"] and again.resolved_forecasts == []


def test_version_avanza_con_cada_cambio(tmp_path):
    ledger = SqliteLedger(Database(tmp_path / "o.db"))
    seen = [ledger.version]
    ledger.record(_f("a"))
    seen.append(ledger.version)
    ledger.resolve("a", "No")
    seen.append(ledger.version)
    ledger.reload()
    seen.append(ledger.version)
    assert seen == sorted(set(seen))  # estrictamente creciente


def test_resolver_inexistente_no_toca_nada(tmp_path):
    ledger = SqliteLedger(Database(tmp_path / "o.db"))
    ledger.record(_f("a"))
    with pytest.raises(KeyError):
        ledger.resolve("zzz", "Sí")
    assert [f.id for f in ledger.open_forecasts] == ["a"]


def test_holt_recupera_una_recta_exacta():
    level, trend, _a, _b, sigma = holt_fit([10.0, 12.0, 14.0, 16.0, 18.0])
    assert level == pytest.approx(18.0) and trend == pytest.approx(2.0)
    assert sigma == pytest.approx(0.0, abs=1e-9)
    with pytest.raises(ValueError):
        holt_fit([1.0, 2.0])
