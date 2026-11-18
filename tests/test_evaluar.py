"""`oraqlo evaluar` y /api/evaluate: ¿qué forma de predecir funciona mejor?"""

from __future__ import annotations

import random
from datetime import timedelta

import pytest

from oraqlo.evaluate import evaluate, format_report
from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.memory.calibration import CalibrationLedger
from test_api import env  # noqa: F401  (fixture compartida)


def _add(ledger, fid, question, source, outcomes, actual=None):
    ledger.record(
        Forecast(
            id=fid,
            question=question,
            distribution=Distribution(outcomes=outcomes),
            horizon=timedelta(days=1),
            source=source,
        )
    )
    if actual is not None:
        ledger.resolve(fid, actual)


def _practice(n: int = 40, seed: int = 3) -> CalibrationLedger:
    """Un comité sobreconfiado (dice 90%, acierta ~60%) con dos miembros: «bueno»
    acierta más que «malo». Y el mercado en las mismas preguntas."""
    rng = random.Random(seed)
    ledger = CalibrationLedger()
    for i in range(n):
        q = f"[polymarket:{100 + i}] ¿?"
        actual = "Yes" if rng.random() < 0.6 else "No"
        _add(ledger, f"c{i}", q, "llm:comite", {"Yes": 0.9, "No": 0.1}, actual)
        good = 0.75 if actual == "Yes" else 0.35
        _add(ledger, f"c{i}::bueno", q, "llm:bueno:miembro", {"Yes": good, "No": 1 - good}, actual)
        _add(ledger, f"c{i}::malo", q, "llm:malo:miembro", {"Yes": 0.5, "No": 0.5}, actual)
        _add(ledger, f"m{i}", q, "polymarket:mercado", {"Yes": 0.6, "No": 0.4}, actual)
    return ledger


def test_evaluar_compara_recalibracion_comite_y_mercado():
    report = evaluate(_practice())
    assert report["n"] == 40  # ni el mercado ni los miembros cuentan como Oraqlo
    [group] = report["models"]
    assert group["model"] == "comite"
    rows = {r["name"]: r for r in group["rows"]}
    # Sobreconfiado: la recalibración nueva, probada en cada caso sin él, mejora.
    assert rows["Recalibración nueva"]["brier"] < rows["Sin recalibrar"]["brier"]
    committee = {r["name"]: r for r in report["committee"]["rows"]}
    assert report["committee"]["questions"] == 40
    assert committee["bueno"]["skill"] > committee["malo"]["skill"]
    assert committee["Comité, pesos aprendidos"]["brier"] < committee["Comité, mismo peso"]["brier"]
    market = {r["name"]: r for r in report["market"]}
    assert market["Oraqlo"]["n"] == market["Mercado (Polymarket)"]["n"] == 40
    text = format_report(report)
    assert "Recalibración nueva" in text and "Frente al mercado" in text


def test_evaluar_sin_datos():
    report = evaluate(CalibrationLedger())
    assert report == {"n": 0, "models": [], "committee": None, "market": None}
    assert "Aún no hay suficientes" in format_report(report)


def test_api_y_cli_de_evaluar(env, monkeypatch, capsys, tmp_path):  # noqa: F811
    client, service, *_ = env
    for i in range(6):
        _add(service.store.ledger, f"f{i}", "¿?", "llm:m:prevision", {"sí": 0.7, "no": 0.3}, "sí")
    data = client.get("/api/evaluate").json()
    assert data["n"] == 6 and data["models"][0]["model"] == "m"
    assert data["models"][0]["rows"][0]["skill"] == pytest.approx(1 - 0.18 / 0.5)

    from oraqlo.cli import main

    monkeypatch.setenv("ORAQLO_HOME", str(tmp_path / "cli"))
    monkeypatch.setenv("ORAQLO_CONFIG", str(tmp_path / "cli" / "none.toml"))
    assert main(["evaluar"]) == 0
    assert "Predicciones válidas resueltas: 0" in capsys.readouterr().out
