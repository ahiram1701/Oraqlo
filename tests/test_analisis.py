"""Análisis completo calibrado y el ciclo de resolver predicciones."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta

import pytest

from oraqlo.casefile import parse_case
from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.llm.base import LLMResponse
from oraqlo.memory.recalibration import model_of, user_resolvable
from test_api import env  # noqa: F401  (fixture compartida)

CASE = {
    "question": "¿Abro una segunda tienda?",
    "horizon_days": 60,
    "actions": {
        "abrir": {"description": "Abrir la tienda", "outcomes": {"sube": 0.6, "baja": 0.4}},
        "esperar": {"description": "Esperar", "outcomes": {"sube": 0.5, "baja": 0.5}},
    },
    "utilities": {"sube": 1.0, "baja": -0.5},
}


def _fake_drafter(monkeypatch, seen: list[str | None]):
    import oraqlo.services.core as core

    def draft(provider, model, question, horizon_days, temperature=0.4, context=None):
        seen.append(context)
        return parse_case({**CASE, "question": question, "horizon_days": horizon_days})

    monkeypatch.setattr(core, "draft_case", draft)


def test_analisis_completo_ancla_el_borrador_y_no_repite_la_prevision(env, monkeypatch):
    _, service, *_ = env
    contexts: list[str | None] = []
    _fake_drafter(monkeypatch, contexts)
    phases: list[str] = []
    result = service.ask_full(
        CASE["question"],
        60,
        research=False,
        emit=lambda t, d: phases.append(d["name"]) if t == "phase" else None,
    )
    # El redactor recibe la previsión calibrada como ancla.
    assert contexts and "Previsión calibrada de Oraqlo" in (contexts[0] or "")
    # Sin investigación el 70% del modelo se suaviza (prudencia sin datos): 66%.
    assert "- sube: 66%" in (contexts[0] or "")
    # Se previó una sola vez (3 muestras), antes de redactar.
    assert phases.index("prever") < phases.index("redactar")
    sources = [f.source for f in service.store.ledger.open_forecasts]
    assert sum(s.endswith(":prevision") for s in sources) == 1
    from oraqlo.forecaster.aggregate import temper_without_data

    assert result["forecast"]["outcomes"] == pytest.approx(
        temper_without_data({"sube": 0.7, "baja": 0.3})
    )


def test_la_probabilidad_del_panel_queda_en_predicciones(env, monkeypatch):
    _, service, _, fake = env
    _fake_drafter(monkeypatch, [])
    original = fake.chat

    def chat(messages, model, **kw):
        # El prompt del Oráculo menciona al Red Team: el falso respondería objeciones.
        if "p_exito" in messages[0].content:
            content = json.dumps({"p_exito": 0.7, "p_objeciones": {"riesgo": 0.2}})
            return LLMResponse(content=content, model=model)
        return original(messages, model, **kw)

    monkeypatch.setattr(fake, "chat", chat)
    result = service.ask_full(CASE["question"], 60, research=False)
    assert result["verdict"]["decision"] == "emitir"
    panel = [f for f in service.store.ledger.open_forecasts if f.source.endswith(":panel")]
    assert len(panel) == 1
    f = panel[0]
    assert f.question.startswith("¿Saldrá bien «")
    assert f.distribution.outcomes == pytest.approx({"sí": 0.7, "no": 0.3})
    assert result["verdict"]["p_success"] == 0.7
    assert model_of(f.source) is not None and user_resolvable(f.source)


def test_fuentes_que_resuelve_el_usuario():
    assert user_resolvable("llm:m:prevision")
    assert user_resolvable("llm:qwen3:8b:panel")
    assert not user_resolvable("llm:m", "[polymarket:1] ¿X?")  # Práctica: se resuelve sola
    assert user_resolvable("llm:m", "¿X?")  # previsión de antes de la 0.7
    assert not user_resolvable("polymarket:mercado")


def _forecast(fid: str, source: str, days_ago: float, horizon: float = 1.0) -> Forecast:
    # La Práctica lleva la etiqueta del mercado en la pregunta, como en autopractice.
    tag = "[polymarket:1] " if fid == "practica" else ""
    return Forecast(
        id=fid,
        question=f"{tag}¿{fid}?",
        distribution=Distribution(outcomes={"sí": 0.6, "no": 0.4}),
        horizon=timedelta(days=horizon),
        assumptions=[],
        source=source,
        created_at=datetime.now(UTC) - timedelta(days=days_ago),
    )


def test_vencidas_primero_contador_y_csv(env):
    client, service, *_ = env
    ledger = service.store.ledger
    ledger.record(_forecast("reciente", "llm:m:prevision", days_ago=0))
    ledger.record(_forecast("vencida", "llm:m:prevision", days_ago=3))
    ledger.record(_forecast("practica", "llm:m", days_ago=3))  # no cuenta: Práctica

    view = client.get("/api/ledger").json()
    assert view["overdue"] == 1
    assert view["open"][0]["id"] == "vencida"
    assert client.get("/api/ledger/overdue").json() == {"count": 1}

    ledger.resolve("reciente", "sí")
    r = client.get("/api/ledger.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text.lstrip("﻿"))))
    by_id = {row["id"]: row for row in rows}
    assert by_id["reciente"]["estado"] == "resuelta" and by_id["reciente"]["resultado"] == "sí"
    assert by_id["vencida"]["estado"] == "abierta" and by_id["vencida"]["previsto"] == "sí"
    assert float(by_id["reciente"]["brier"]) >= 0


def test_aviso_de_predicciones_vencidas_una_vez(env, monkeypatch):
    _, service, app, _ = env
    service.store.ledger.record(_forecast("vencida", "llm:m:prevision", days_ago=3))
    sent: list[tuple] = []
    push = app.state.push
    monkeypatch.setattr(push, "send", lambda *a, **k: sent.append((a, k)) or 1)
    app.state.notify_overdue()
    app.state.notify_overdue()  # la misma predicción no se avisa dos veces
    due = [a for a, k in sent if a[0] == "Predicciones para resolver"]
    assert len(due) == 1 and "1 predicción ya debería saberse" in due[0][1]
