"""Vigilancia: señales, re-predicción con novedades, cadena de versiones y avisos."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from oraqlo.llm.base import LLMResponse
from oraqlo.research import Fact, ResearchBrief
from oraqlo.services.watch import Signal, Watch, WatchStore, moved, signals_from_texts
from test_api import env  # noqa: F401  (fixture compartida)


@pytest.fixture
def vigia(env, monkeypatch):  # noqa: F811
    """La app con búsqueda web «disponible», investigación falsa y un modelo que sabe
    hacer de Vigía. `state` controla qué devuelve cada cosa."""
    client, service, _app, fake = env
    monkeypatch.setattr("oraqlo.services.core.OllamaWebClient.is_available", lambda self: True)
    state = {
        "facts": ["El banco central subió tipos."],
        "relevant": True,
        "triggered": [1],
        "outcomes": {"sube": 0.7, "baja": 0.3},
    }

    def research(question, horizon_days, emit=None, resume=None):
        return ResearchBrief(facts=[Fact(t, "https://ej.com") for t in state["facts"]], queries=[])

    monkeypatch.setattr(service, "research", research)
    original = fake.chat

    def chat(messages, model, temperature=0.7, json_mode=False, **kwargs):
        system = messages[0].content
        if '"signals"' in system:
            out = {
                "signals": [
                    {
                        "text": "Que suban los tipos",
                        "look_for": "tipos de interés",
                        "favors": "baja",
                    },
                    {"text": "Que caiga el paro", "look_for": "paro", "favors": "sube"},
                ]
            }
        elif '"relevant"' in system:
            out = {
                "relevant": state["relevant"],
                "summary": "Subieron los tipos.",
                "triggered": state["triggered"],
            }
        elif '"outcomes"' in system and "Explicas" not in system:
            out = {"outcomes": state["outcomes"], "assumptions": [], "rationale": "r"}
        else:
            return original(messages, model, temperature, json_mode, **kwargs)
        fake.calls += 1
        return LLMResponse(content=json.dumps(out), model=model)

    monkeypatch.setattr(fake, "chat", chat)
    return client, service, state


def _make_due(service, root):
    w = service.watches.get(root)
    w.created_at = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    w.last_check = None
    service.watches.save(w)


def test_una_prevision_nueva_queda_vigilada_con_sus_senales(vigia):
    _, service, _ = vigia
    result = service.ask_forecast("¿Sube la bolsa?", 60, research=False)
    assert result["watch"]["enabled"] is True
    assert [s["text"] for s in result["watch"]["signals"]] == [
        "Que suban los tipos",
        "Que caiga el paro",
    ]
    assert result["watch"]["signals"][0]["favors"] == "baja"
    # Recién hecha no toca revisarla todavía.
    assert service.watch_due() == []


def test_con_novedades_vuelve_a_predecir_encadena_y_avisa(vigia):
    _, service, state = vigia
    first = service.ask_forecast("¿Sube la bolsa?", 60, research=False)["forecast"]["id"]
    _make_due(service, first)
    state["outcomes"] = {"sube": 0.3, "baja": 0.7}
    out = service.watch_run()
    assert out["checked"] == 1 and len(out["updated"]) == 1
    change = out["updated"][0]
    assert change["moved"] > 0.3  # la primera iba suavizada (sin datos)
    assert change["triggered"] == ["Que suban los tipos"]
    assert out["alerts"][0]["title"] == "Cambió una previsión"
    assert "Subieron los tipos" in out["alerts"][0]["body"]

    w = service.watches.get(first)
    assert len(w.chain) == 2 and w.signals[0].triggered_at is not None
    latest = w.latest
    new = next(f for f in service.store.ledger.open_forecasts if f.id == latest)
    assert any(a.startswith(f"Actualiza la previsión {first}") for a in new.assumptions)

    # La lista enseña solo la versión vigente, con su historia.
    view = service.ledger_view()
    assert [f["id"] for f in view["open"]] == [latest]
    assert view["open"][0]["watch"]["versions"] == 2

    # Justo después no vuelve a tocar.
    assert service.watch_run()["checked"] == 0

    # Resolver la vigente resuelve también la vieja: cada una con lo que se sabía.
    resolved = service.resolve(latest, "baja")
    assert {r["id"] for r in resolved} >= {first, latest}
    assert not [f for f in service.store.ledger.open_forecasts if f.id in (first, latest)]
    assert service.watches.get(first).enabled is False


def test_sin_hechos_nuevos_no_gasta_en_predecir(vigia):
    _, service, _state = vigia
    first = service.ask_forecast("¿Sube?", 60, research=False)["forecast"]["id"]
    _make_due(service, first)
    service.watch_run()  # primera vez: todo es nuevo
    _make_due(service, first)
    out = service.watch_run()  # mismos hechos: nada que hacer
    assert out["checked"] == 1 and out["updated"] == []
    assert len(service.watches.get(first).chain) <= 2


def test_novedad_irrelevante_no_cambia_nada(vigia):
    _, service, state = vigia
    first = service.ask_forecast("¿Sube?", 60, research=False)["forecast"]["id"]
    _make_due(service, first)
    state.update(relevant=False, triggered=[])
    out = service.watch_run()
    assert out["updated"] == [] and len(service.watches.get(first).chain) == 1
    assert service.watches.get(first).facts == ["El banco central subió tipos."]


def test_presupuesto_diario(vigia):
    _, service, _ = vigia
    first = service.ask_forecast("¿Sube?", 60, research=False)["forecast"]["id"]
    _make_due(service, first)
    service.set_options({"watch_daily_budget": 0})
    assert service.watch_due() == []


def test_vigilancia_apagada_en_opciones(vigia):
    _, service, _ = vigia
    service.set_options({"watch": False})
    assert service.ask_forecast("¿Sube?", 60, research=False)["watch"] is None


def test_descartar_borra_todas_las_versiones(vigia):
    _, service, state = vigia
    first = service.ask_forecast("¿Sube?", 60, research=False)["forecast"]["id"]
    _make_due(service, first)
    state["outcomes"] = {"sube": 0.2, "baja": 0.8}
    service.watch_run()
    latest = service.watches.get(first).latest
    service.discard(latest)
    assert not service.store.ledger.open_forecasts
    assert service.watches.get(first) is None


def test_vigilar_un_objetivo_hace_checkin_automatico(vigia):
    from oraqlo.goaltrack import TrackedGoal

    _, service, _ = vigia
    goal = TrackedGoal(
        id="g1",
        goal="Correr un maratón",
        created_at=datetime.now(UTC).isoformat(),
        deadline=(datetime.now(UTC) + timedelta(days=100)).date().isoformat(),
        summary="plan",
        steps=[],
        milestones=[],
        initial_p=0.2,
    )
    service.store.goals.add(goal)
    service.watches.save(
        Watch(
            root="f-goal",
            question="Correr un maratón",
            horizon_days=100,
            created_at=(datetime.now(UTC) - timedelta(days=2)).isoformat(),
            chain=["f-goal"],
            signals=[Signal("Que haga buen tiempo")],
            kind="goal",
            goal_id="g1",
        )
    )
    out = service.watch_run()
    assert out["updated"][0]["after"]["se logra"] == pytest.approx(0.55)
    assert out["alerts"][0]["url"] == "/objetivos/g1"
    checkin = service.store.goals.get("g1").checkins[-1]
    assert checkin.note.startswith("Vigilancia automática")


def test_api_de_opciones_y_vigilancia(env):  # noqa: F811
    client, service, *_ = env
    options = client.get("/api/options").json()
    assert options["markets"] is True and options["watch_daily_budget"] == 5
    changed = client.put("/api/options", json={"markets": False, "watch_alert_points": 20}).json()
    assert changed["markets"] is False and changed["watch_alert_points"] == 20
    assert client.put("/api/options", json={"watch_daily_budget": 1000}).status_code == 422

    fid = service.ask_forecast("¿Sube?", 30, research=False)["forecast"]["id"]
    # Sin búsqueda web no se puede vigilar.
    response = client.put(f"/api/ledger/{fid}/watch", json={"enabled": True})
    assert response.status_code == 409
    assert client.put("/api/ledger/nada/watch", json={"enabled": True}).status_code == 404
    assert client.post("/api/watch/run").status_code == 409


def test_almacen_de_vigilancias(env):  # noqa: F811
    _, service, *_ = env
    store = WatchStore(service.store.settings)
    w = Watch(root="a", question="q", horizon_days=10, created_at="2026-01-01T00:00:00+00:00",
              chain=["a", "b", "c"])  # fmt: skip
    store.save(w)
    assert store.find("b").root == "a"
    assert store.superseded() == {"a", "b"}
    store.delete("a")
    assert store.all() == [] and store.find("b") is None


def test_utilidades():
    assert moved({"a": 0.5, "b": 0.5}, {"a": 0.8, "b": 0.2}) == pytest.approx(0.3)
    signals = signals_from_texts(["Sube el paro", "sube  el paro", "", "Baja la luz"])
    assert [s.text for s in signals] == ["Sube el paro", "Baja la luz"]


def test_cada_version_se_resuelve_con_sus_propios_escenarios(vigia):
    """Si la versión nueva cambió los escenarios, la vieja no se puntúa con una etiqueta
    que nunca contempló: se descarta. Lo imprevisto para todas cuenta para todas."""
    _, service, state = vigia
    first = service.ask_forecast("¿Sube?", 60, research=False)["forecast"]["id"]
    _make_due(service, first)
    state["outcomes"] = {"sube mucho": 0.2, "sube poco": 0.3, "baja": 0.5}
    service.watch_run()
    latest = service.watches.get(first).latest
    resolved = service.resolve(latest, "sube mucho")
    ids = {r["id"] for r in resolved}
    assert latest in ids and first not in ids
    assert all(f.id != first for f in service.store.ledger.open_forecasts)


def test_lo_imprevisto_puntua_en_todas_las_versiones(vigia):
    _, service, state = vigia
    first = service.ask_forecast("¿Sube?", 60, research=False)["forecast"]["id"]
    _make_due(service, first)
    state["outcomes"] = {"sube mucho": 0.2, "sube poco": 0.3, "baja": 0.5}
    service.watch_run()
    latest = service.watches.get(first).latest
    ids = {r["id"] for r in service.resolve(latest, "se suspendió la bolsa")}
    assert {first, latest} <= ids
