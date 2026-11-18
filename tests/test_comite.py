"""Comité de modelos, matemáticas de la recalibración y mejor información en la Práctica."""

from __future__ import annotations

import json
import random
import threading
from datetime import UTC, datetime, timedelta

import pytest

from oraqlo.autopractice import PracticeEngine, choose_markets, duels
from oraqlo.forecaster.aggregate import parse_base_rate, pool_log_odds, temper_without_data
from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.forecaster.committee import (
    LENSES,
    Member,
    build_members,
    family,
    model_weights,
    pick_committee,
)
from oraqlo.forecaster.llm import LLMForecaster
from oraqlo.llm.base import LLMProvider, LLMResponse
from oraqlo.memory.calibration import CalibrationLedger
from oraqlo.memory.recalibration import MIN_RESOLVED, fit, for_model
from oraqlo.polymarket import Market
from oraqlo.world_model import WorldModel
from test_api import env  # noqa: F401  (fixture compartida)


class ByModel(LLMProvider):
    """Responde según el modelo; un modelo en `broken` falla. Seguro entre hilos."""

    def __init__(self, replies: dict[str, dict], broken: set[str] = frozenset()):
        self.replies = replies
        self.broken = broken
        self.calls: list[tuple[str, list]] = []
        self._lock = threading.Lock()

    def chat(self, messages, model, temperature=0.7, json_mode=False, **kwargs):
        with self._lock:
            self.calls.append((model, list(messages)))
        if model in self.broken:
            raise RuntimeError(f"{model} caído")
        return LLMResponse(content=json.dumps(self.replies[model]), model=model)


def _market(mid="1", yes=0.4, event="ev-1", description="Se resuelve «Yes» si pasa X.") -> Market:
    return Market(
        id=mid,
        question="¿Pasará X?",
        outcomes={"Yes": yes, "No": 1 - yes},
        end_date=datetime.now(UTC) + timedelta(days=10),
        closed=False,
        resolved=False,
        volume_24h=50_000,
        raw={"description": description, "events": [{"slug": event}]},
    )


class Markets:
    def __init__(self, markets, by_id=None):
        self.markets, self.by_id = markets, by_id or {}

    def list_open(self, limit=20, **kw):
        return self.markets[:limit]

    def get_market(self, mid):
        return self.by_id[mid]


# ── elegir el comité ─────────────────────────────────────────────────────


def test_familias_y_comite_recomendado():
    assert family("deepseek-v4-pro:0813") == "deepseek"
    assert family("gpt-oss:120b-cloud") == "gpt-oss"
    assert family("kimi-k3.1-pro") == "kimi"
    catalog = [
        {"name": "gpt-oss:120b", "size": 65},
        {"name": "gpt-oss:20b", "size": 13},
        {"name": "deepseek-v4.1-flash", "size": 486},
        {"name": "deepseek-v4-pro:0813", "size": 892},
        {"name": "kimi-k2.7-code", "size": 595},
        {"name": "kimi-k3", "size": 1560},
        {"name": "glm-5.3", "size": 755},
        {"name": "beam", "size": 0},  # familia desconocida: no entra
    ]
    # Una por familia, la grande y no especializada; nunca la familia del Oráculo.
    assert pick_committee(catalog, "deepseek-v4.1-flash:cloud") == [
        "kimi-k3",
        "glm-5.3",
        "gpt-oss:120b",
    ]
    assert pick_committee(catalog, "gpt-oss:120b", size=2) == ["deepseek-v4-pro:0813"]


def test_un_solo_modelo_varia_el_enfoque():
    p = ByModel({})
    members = build_members((p, "m"), [], 3)
    assert [m.model for m in members] == ["m", "m", "m"]
    assert [m.lens for m in members] == list(LENSES[:3])
    # Con comité, al menos uno por modelo, cada uno con su enfoque.
    members = build_members((p, "a"), [(p, "b"), (p, "c"), (p, "a")], 1)
    assert [m.model for m in members] == ["a", "b", "c"]


# ── pesos aprendidos ─────────────────────────────────────────────────────


def _member_history(ledger: CalibrationLedger, model: str, hit_rate: float, n: int, seed: int):
    rng = random.Random(seed)
    for i in range(n):
        fid = f"{model}-{i}"
        ledger.record(
            Forecast(
                id=fid,
                question=f"[polymarket:{seed}{i}] ¿?",
                distribution=Distribution(outcomes={"Yes": 0.8, "No": 0.2}),
                horizon=timedelta(days=1),
                source=f"llm:{model}:miembro",
            )
        )
        ledger.resolve(fid, "Yes" if rng.random() < hit_rate else "No")


def test_pesa_mas_el_modelo_que_mas_acierta():
    ledger = CalibrationLedger()
    assert model_weights(ledger, ["a", "b"]) == {"a": 1.0, "b": 1.0}  # sin historial, iguales
    _member_history(ledger, "bueno", 0.9, 40, 1)
    _member_history(ledger, "malo", 0.4, 40, 2)
    w = model_weights(ledger, ["bueno", "malo", "nuevo"])
    assert w["bueno"] > w["nuevo"] == 1.0 > w["malo"]
    pooled = pool_log_odds(
        [{"sí": 0.8, "no": 0.2}, {"sí": 0.3, "no": 0.7}], [w["bueno"], w["malo"]]
    )
    assert pooled["sí"] > pool_log_odds([{"sí": 0.8, "no": 0.2}, {"sí": 0.3, "no": 0.7}])["sí"]


def test_comite_en_el_oraculo_combina_y_sobrevive_a_un_fallo():
    reply = lambda p: {"outcomes": {"sí": p, "no": 1 - p}, "assumptions": [], "rationale": "r"}
    provider = ByModel({"a": reply(0.7), "b": reply(0.6), "c": reply(0.2)}, broken={"c"})
    members = [Member(provider, m, LENSES[i]) for i, m in enumerate("abc")]
    progress: list[str] = []
    oracle = LLMForecaster(provider, "a", members=members, on_progress=progress.append)
    f = oracle.forecast(WorldModel().current(), "¿pasa?", timedelta(days=30))
    assert set(oracle.last_members) == {"a", "b"}
    assert 0.6 < f.distribution.outcomes["sí"] < 0.7
    assert any(a.startswith("Comité de 2 modelos") for a in f.assumptions)
    assert any("c no respondió" in msg for msg in progress)
    # Cada miembro recibe su enfoque y los escenarios del primero.
    sent_b = next(m for model, m in provider.calls if model == "b")
    assert LENSES[1] in sent_b[-1].content and '"sí", "no"' in sent_b[-1].content


# ── tasa base numérica ───────────────────────────────────────────────────


def test_sin_datos_se_acerca_a_la_tasa_base():
    p = {"Yes": 0.7, "No": 0.3}
    assert temper_without_data(p, {"Yes": 0.5, "No": 0.5}) == pytest.approx(temper_without_data(p))
    toward_low_base = temper_without_data(p, {"Yes": 0.1, "No": 0.9})
    assert toward_low_base["Yes"] < temper_without_data(p)["Yes"]
    assert parse_base_rate({"yes ": 1, "NO": 3}, ["Yes", "No"]) == {"Yes": 0.25, "No": 0.75}
    assert parse_base_rate({"Yes": 1}, ["Yes", "No"]) is None
    assert parse_base_rate("una de cada tres", ["Yes", "No"]) is None


# ── recalibración honesta ────────────────────────────────────────────────


def _overconfident(n: int, seed: int = 7):
    rng = random.Random(seed)
    return [({"sí": 0.9, "no": 0.1}, "sí" if rng.random() < 0.6 else "no") for _ in range(n)]


def test_recalibracion_desde_15_y_solo_si_valida():
    rc = fit(_overconfident(MIN_RESOLVED + 25))
    assert MIN_RESOLVED == 15 and rc.active and rc.a < 1
    # Bien calibrado y con pocos casos: la validación cruzada no ve mejora → no se toca.
    rng = random.Random(5)
    calibrated = [({"sí": 0.7, "no": 0.3}, "sí" if rng.random() < 0.7 else "no") for _ in range(20)]
    assert fit(calibrated).a == 1.0


def test_recalibracion_ignora_mercados_ya_decididos():
    ledger = CalibrationLedger()
    for i, (outcomes, actual) in enumerate(_overconfident(40)):
        mid = f"9{i}"
        ledger.record(
            Forecast(
                id=f"o{i}",
                question=f"[polymarket:{mid}] ¿?",
                distribution=Distribution(outcomes=outcomes),
                horizon=timedelta(days=1),
                source="llm:m",
            )
        )
        ledger.record(  # el mercado ya lo sabía: esa pregunta no enseña nada
            Forecast(
                id=f"m{i}",
                question=f"[polymarket:{mid}] ¿?",
                distribution=Distribution(outcomes={"sí": 0.99, "no": 0.01}),
                horizon=timedelta(days=1),
                source="polymarket:mercado",
            )
        )
        ledger.resolve(f"o{i}", actual)
    assert for_model(ledger, "m").n == 0


# ── la Práctica ──────────────────────────────────────────────────────────


def test_practica_con_comite_registra_miembros_y_reglas():
    reply = lambda p: {"outcomes": {"Yes": p, "No": 1 - p}, "assumptions": ["s"], "base_rate": "b"}
    provider = ByModel({"a": reply(0.6), "b": reply(0.5)})
    members = [Member(provider, "a", LENSES[0]), Member(provider, "b", LENSES[1])]
    ledger = CalibrationLedger()
    engine = PracticeEngine(
        Markets([_market("7")]), provider, "a", ledger, members=members, weights={"a": 1, "b": 1}
    )
    [(market, forecast)] = engine.predict_new(n=1, research=False)
    assert forecast.source == "llm:comite"
    sources = sorted(f.source for f in ledger.open_forecasts)
    assert sources == ["llm:a:miembro", "llm:b:miembro", "llm:comite", "polymarket:mercado"]
    assert all(
        f.id.startswith(forecast.id + "::") for f in ledger.open_forecasts if "miembro" in f.source
    )
    # Las reglas del mercado llegan al prompt.
    assert "Reglas de resolución:\nSe resuelve «Yes» si pasa X." in provider.calls[0][1][-1].content
    # El duelo es entre el comité y el mercado (los miembros no cuentan como Oraqlo).
    [item] = duels(ledger)["items"]
    assert item["oraqlo_p"] == pytest.approx(forecast.distribution.outcomes["Yes"])
    # Al resolverse, se cierran todos (los miembros llevan la etiqueta del mercado).
    market.resolved, market.winner = True, "Yes"
    engine.market_client = Markets([], {"7": market})
    assert len(engine.resolve_due()) == 4


def test_elige_mercados_inciertos_y_uno_por_evento():
    markets = [
        _market("1", yes=0.95, event="a"),  # casi decidido
        _market("2", yes=0.5, event="b"),
        _market("3", yes=0.4, event="b"),  # otra línea del mismo partido
        _market("4", yes=0.3, event="c"),
    ]
    assert [m.id for m in choose_markets(markets, 3)] == ["2", "4", "1"]
    assert [m.id for m in choose_markets(markets, 2)] == ["2", "4"]


def test_la_investigacion_de_practica_no_mira_polymarket():
    from oraqlo.research import Researcher

    r = Researcher(None, None, "m", exclude_domains=("polymarket.com",))  # type: ignore[arg-type]
    assert not r._allowed("https://polymarket.com/event/x")
    assert not r._allowed("https://www.polymarket.com/event/x")
    assert r._allowed("https://www.espn.com/nba")
    assert r._allowed("https://notpolymarket.com/x")


# ── en la app ────────────────────────────────────────────────────────────


def test_miembros_fuera_de_predicciones_y_del_boletin(env):  # noqa: F811
    client, service, *_ = env
    ledger = service.store.ledger
    for fid, source, p in (("p1", "llm:comite", 0.7), ("p1::a", "llm:a:miembro", 0.9)):
        ledger.record(
            Forecast(
                id=fid,
                question="[polymarket:55] ¿?",
                distribution=Distribution(outcomes={"Yes": p, "No": 1 - p}),
                horizon=timedelta(days=1),
                source=source,
            )
        )
    service.resolve("p1", "Yes")
    view = client.get("/api/ledger").json()
    assert [r["id"] for r in view["resolved"]] == ["p1"]
    cal = client.get("/api/calibration").json()
    assert cal["n"] == 1
    refs = [s for s in cal["sources"] if s.get("reference")]
    assert [s["source"] for s in refs] == ["llm:a:miembro"]
    # Borrar la predicción borra también lo que dijo cada miembro.
    assert client.delete("/api/ledger/resolved/p1").status_code == 204
    assert ledger.resolved_forecasts == []


def test_ajustes_del_comite(env, monkeypatch):  # noqa: F811
    client, service, *_ = env
    monkeypatch.setenv("OLLAMA_API_KEY", "k")
    monkeypatch.setattr(
        "oraqlo.services.core.fetch_cloud_catalog",
        lambda *a, **k: [{"name": "kimi-k3", "size": 9}, {"name": "glm-5.3", "size": 8}],
    )
    view = client.get("/api/committee").json()
    assert view["available"] and view["mode"] == "auto"
    assert view["recommended"] == view["models"] == ["kimi-k3", "glm-5.3"]
    view = client.put("/api/committee", json={"mode": "custom", "models": ["glm-5.3"]}).json()
    assert view["mode"] == "custom" and view["models"] == ["glm-5.3"]
    assert service.config.forecast.committee == ["glm-5.3"]
    assert client.put("/api/committee", json={"mode": "off"}).json()["models"] == []
    assert client.put("/api/committee", json={"mode": "auto"}).json()["mode"] == "auto"
    assert service.config.forecast.committee is None
    assert client.put("/api/committee", json={"mode": "custom", "models": []}).status_code == 400
