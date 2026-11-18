"""Precisión: mercados como voz, clase de referencia, estimación por partes,
calibración por tipo de pregunta y preguntas de cantidad."""

from __future__ import annotations

import json
import math
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from oraqlo.forecaster import numeric
from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.forecaster.committee import LENSES, model_weights
from oraqlo.forecaster.decompose import Decomposition
from oraqlo.forecaster.llm import LLMForecaster
from oraqlo.forecaster.markets import (
    ManifoldSource,
    MarketFinder,
    MetaculusSource,
    Quote,
    map_outcomes,
)
from oraqlo.llm.base import LLMProvider, LLMResponse
from oraqlo.memory.calibration import CalibrationLedger
from oraqlo.memory.recalibration import (
    MARKET_SOURCE,
    MIN_RESOLVED,
    fit,
    group_key,
    raw_of,
)
from oraqlo.memory.reference import reference_block, similar
from oraqlo.world_model import WorldModel
from test_api import env  # noqa: F401  (fixture compartida)


class Scripted(LLMProvider):
    """Responde según una palabra clave del prompt de sistema; guarda los mensajes."""

    def __init__(self, replies: dict[str, dict]):
        self.replies = replies
        self.seen: list[list] = []

    def chat(self, messages, model, temperature=0.7, json_mode=False, **kwargs):
        self.seen.append(list(messages))
        system = messages[0].content
        for key, reply in self.replies.items():
            if key in system:
                return LLMResponse(content=json.dumps(reply), model=model)
        raise AssertionError(f"prompt inesperado: {system[:80]}")


class FakeSource:
    name = "polymarket"

    def __init__(self, quotes):
        self.quotes = quotes
        self.queries: list[str] = []

    def search(self, query, limit=8):
        self.queries.append(query)
        return list(self.quotes)


def _quote(yes=0.8, question="Will X happen by June?"):
    return Quote("polymarket", "123", question, {"Yes": yes, "No": 1 - yes}, url="https://x")


ORACLE = {"outcomes": {"sí": 0.4, "no": 0.6}, "assumptions": [], "rationale": "r"}


# ── mercados ───────────────────────────────────────────────────────────────


def test_emparejar_resultados_del_mercado():
    market = {"Yes": 0.7, "No": 0.3}
    assert map_outcomes(["sí", "no"], market, {"sí": "Yes", "no": "No"}) == {
        "sí": 0.7,
        "no": pytest.approx(0.3),
    }
    # Repetir resultado, inventarlo o dejar fuera mucha probabilidad: no vale.
    assert map_outcomes(["sí", "no"], market, {"sí": "Yes", "no": "Yes"}) is None
    assert map_outcomes(["sí", "no"], market, {"sí": "Quizá", "no": "No"}) is None
    three = {"A": 0.5, "B": 0.3, "C": 0.2}
    assert map_outcomes(["a", "b"], three, {"a": "A", "b": "B"}) is None


def test_buscador_encuentra_el_mercado_equivalente():
    llm = Scripted(
        {
            "queries": {"queries": ["X June"]},
            "COINCIDE": {"match": {"ref": "M1", "mapping": {"sí": "Yes", "no": "No"}}},
        }
    )
    finder = MarketFinder(llm, "m", sources=[FakeSource([_quote(0.8)])])
    voice = finder.find("¿Pasará X antes de junio?", timedelta(days=60), ["sí", "no"])
    assert voice is not None
    assert voice.voice == "mercado:polymarket"
    assert voice.outcomes["sí"] == pytest.approx(0.8)


def test_buscador_sin_coincidencia_o_sin_fuentes():
    llm = Scripted({"queries": {"queries": ["X"]}, "COINCIDE": {"match": None}})
    finder = MarketFinder(llm, "m", sources=[FakeSource([_quote()])])
    assert finder.find("¿Pasará X?", timedelta(days=30), ["sí", "no"]) is None
    # Sin fuentes no se gasta ni una llamada al modelo.
    empty = Scripted({})
    assert MarketFinder(empty, "m", sources=[]).find("¿X?", timedelta(days=9), ["a", "b"]) is None
    assert empty.seen == []


def test_manifold_filtra_mercados_con_poca_gente():
    def fetch(url, headers=None):
        assert "search-markets" in url
        return [
            {"id": "a", "question": "Q1", "outcomeType": "BINARY", "probability": 0.3,
             "uniqueBettorCount": 40, "url": "https://m/a", "closeTime": 1_900_000_000_000},
            {"id": "b", "question": "Q2", "outcomeType": "BINARY", "probability": 0.9,
             "uniqueBettorCount": 3},
        ]  # fmt: skip

    quotes = ManifoldSource(fetch=fetch).search("q")
    assert [q.id for q in quotes] == ["a"]
    assert quotes[0].outcomes == {"Yes": 0.3, "No": pytest.approx(0.7)}


def test_metaculus_sin_token_no_consulta():
    def fetch(url, headers=None):
        raise AssertionError("no debía consultar")

    assert MetaculusSource(token="", fetch=fetch).search("q") == []


def test_el_mercado_entra_como_voz_y_cuenta_como_dato():
    llm = Scripted({'"outcomes"': ORACLE})

    class Finder:
        def find(self, question, horizon, labels):
            from oraqlo.forecaster.markets import MarketVoice

            return MarketVoice(_quote(0.9), {"sí": 0.9, "no": 0.1}, "misma pregunta")

    oracle = LLMForecaster(
        llm, "m", market_finder=Finder(), weigh=lambda names: dict.fromkeys(names, 1.0)
    )
    f = oracle.forecast(WorldModel().current(), "¿Pasará X?", timedelta(days=30))
    # 0.4 y 0.9 a partes iguales en log-odds: claramente por encima del 0.4 del modelo.
    assert f.distribution.outcomes["sí"] > 0.6
    assert set(oracle.last_members) == {"m", "mercado:polymarket"}
    assert any(a.startswith("Mercado de predicción polymarket") for a in f.assumptions)
    # Con mercado hay un dato: no se suaviza como «solo intuición».
    assert not any("Sin datos concretos" in a for a in f.assumptions)


def test_el_peso_del_mercado_sale_de_su_historial_en_la_practica():
    ledger = CalibrationLedger()
    now = datetime.now(UTC)
    for i in range(30):
        f = Forecast(
            id=str(uuid.uuid4()),
            question=f"[polymarket:{i}] ¿Pasará {i}?",
            distribution=Distribution(outcomes={"Yes": 0.85, "No": 0.15}),
            horizon=timedelta(days=5),
            source=MARKET_SOURCE,
            created_at=now,
        )
        ledger.record(f)
        ledger.resolve(f.id, "Yes")
    weights = model_weights(ledger, ["mercado:polymarket", "modelo-sin-historial"])
    assert weights["mercado:polymarket"] > 1.5
    assert weights["modelo-sin-historial"] == 1.0


def test_previsiones_normales_registran_cada_voz_y_se_resuelven_juntas(env, monkeypatch):  # noqa: F811
    _, service, _, _ = env

    class Finder:
        def __init__(self, *a, **k):
            pass

        def find(self, question, horizon, labels):
            from oraqlo.forecaster.markets import MarketVoice

            return MarketVoice(_quote(0.9), dict(zip(labels, (0.9, 0.1), strict=False)))

    monkeypatch.setattr("oraqlo.forecaster.markets.MarketFinder", Finder)
    result = service.ask_forecast("¿Sube?", 30, research=False)
    fid = result["forecast"]["id"]
    assert result["voices"]["market"]["platform"] == "polymarket"
    ids = {f.id for f in service.store.ledger.open_forecasts}
    assert f"{fid}::mercado:polymarket" in ids
    # Lo que dijo cada voz no sale en la lista ni como «para resolver».
    view = service.ledger_view()
    assert [f["id"] for f in view["open"]] == [fid]
    service.resolve(fid, "sube")
    assert not service.store.ledger.open_forecasts


# ── clase de referencia ───────────────────────────────────────────────────


def _resolved(ledger, question, outcomes, actual, source="llm:m:prevision"):
    f = Forecast(
        id=str(uuid.uuid4()),
        question=question,
        distribution=Distribution(outcomes=outcomes),
        horizon=timedelta(days=30),
        source=source,
        created_at=datetime.now(UTC),
    )
    ledger.record(f)
    ledger.resolve(f.id, actual)


def test_clase_de_referencia_encuentra_lo_parecido():
    ledger = CalibrationLedger()
    _resolved(
        ledger, "¿Subirá el bitcoin de 100.000 dólares en marzo?", {"sí": 0.8, "no": 0.2}, "no"
    )
    _resolved(ledger, "¿Lloverá en Madrid el domingo?", {"sí": 0.3, "no": 0.7}, "no")
    found = similar(ledger, "¿Superará el bitcoin los 120.000 dólares en abril?")
    assert [s.question for s in found] == ["¿Subirá el bitcoin de 100.000 dólares en marzo?"]
    block = reference_block(ledger, "¿Superará el bitcoin los 120.000 dólares?")
    assert "fallaste" in block and "le diste 20%" in block
    assert reference_block(ledger, "¿Ganará el Betis la liga?") == ""


def test_la_referencia_solo_va_a_la_vision_externa():
    llm = Scripted({'"outcomes"': ORACLE})
    oracle = LLMForecaster(llm, "m", samples=2, reference=lambda q: "PREGUNTAS PARECIDAS: x")
    oracle.forecast(WorldModel().current(), "¿X?", timedelta(days=10))
    users = [msgs[1].content for msgs in llm.seen]
    with_ref = [u for u in users if "PREGUNTAS PARECIDAS" in u]
    assert len(with_ref) == 1 and LENSES[0] in with_ref[0]


# ── estimación por partes ─────────────────────────────────────────────────


def test_partes_y_u_o():
    both = Decomposition("y", "sí", ["abre", "es rentable"], [0.5, 0.4])
    assert both.p_yes == pytest.approx(0.2)
    either = Decomposition("o", "sí", ["vía A", "vía B"], [0.5, 0.4])
    assert either.p_yes == pytest.approx(0.7)
    assert both.outcomes(["sí", "no"]) == {"sí": pytest.approx(0.2), "no": pytest.approx(0.8)}
    assert "hacen falta todas" in both.note()


@pytest.mark.voces
def test_descomponer_con_el_modelo():
    from oraqlo.forecaster.decompose import decompose

    llm = Scripted(
        {
            "COMPUESTA": {
                "compound": True,
                "mode": "y",
                "yes_label": "Sí",
                "parts": ["¿Abre?", "¿Es rentable si abre?"],
            },
            "CONDICIONADA": {"p": [0.6, 0.5], "rationale": "r"},
        }
    )
    d = decompose("¿Abre y es rentable?", timedelta(days=90), ["sí", "no"], (llm, "a"), (llm, "b"))
    assert d is not None and d.yes_label == "sí" and d.p_yes == pytest.approx(0.3)
    simple = Scripted({"COMPUESTA": {"compound": False}})
    assert (
        decompose("¿Llueve?", timedelta(days=1), ["sí", "no"], (simple, "a"), (simple, "b")) is None
    )
    assert len(simple.seen) == 1  # simple: no se gasta la segunda llamada
    assert (
        decompose("¿Quién gana?", timedelta(days=1), ["A", "B", "C"], (llm, "a"), (llm, "b"))
        is None
    )


@pytest.mark.voces
def test_la_estructura_entra_como_voz():
    llm = Scripted(
        {
            '"outcomes"': ORACLE,
            "COMPUESTA": {
                "compound": True,
                "mode": "y",
                "yes_label": "sí",
                "parts": ["uno", "dos"],
            },
            "CONDICIONADA": {"p": [0.5, 0.5]},
        }
    )
    oracle = LLMForecaster(llm, "m", decompose=True)
    f = oracle.forecast(WorldModel().current(), "¿Uno y dos?", timedelta(days=30))
    assert "estructura" in oracle.last_members
    assert oracle.last_extras["structure"]["p_yes"] == pytest.approx(0.25)
    assert any(a.startswith("Por partes") for a in f.assumptions)


# ── calibración por tipo de pregunta ──────────────────────────────────────


def test_factor_propio_para_el_tipo_que_se_pasa_de_confiado():
    pairs, groups = [], []
    # Corto plazo: dice 90% y acierta la mitad (sobreconfiado).
    for i in range(24):
        pairs.append(({"sí": 0.9, "no": 0.1}, "sí" if i % 2 else "no"))
        groups.append(group_key("prevision", 2, 10))
    # Largo plazo: dice 60% y acierta el 90% (se queda corto).
    for i in range(40):
        pairs.append(({"sí": 0.6, "no": 0.4}, "sí" if i % 10 < 9 else "no"))
        groups.append(group_key("prevision", 2, 400))
    recal = fit(pairs, groups)
    short, long = group_key("prevision", 2, 10), group_key("prevision", 2, 400)
    assert recal.n == 64 >= MIN_RESOLVED
    assert recal.factor(short) < 1.0 < recal.factor(long)
    assert recal.apply({"sí": 0.9, "no": 0.1}, short)["sí"] < 0.9
    assert recal.apply({"sí": 0.6, "no": 0.4}, long)["sí"] > 0.6
    assert recal.brier_after < recal.brier_before


def test_un_grupo_con_poca_diferencia_no_tiene_factor_propio():
    """La validación cruzada protege: si el grupo apenas se distingue, usa el global."""
    pairs = [({"sí": 0.7, "no": 0.3}, "sí" if i % 10 < 7 else "no") for i in range(30)]
    recal = fit(pairs, [group_key("prevision", 2, 400)] * 30)
    assert recal.groups == {}


def test_la_nota_por_grupo_se_puede_deshacer():
    pairs = [({"sí": 0.9, "no": 0.1}, "sí" if i % 2 else "no") for i in range(30)]
    recal = fit(pairs, [group_key("prevision", 2, 10)] * 30)
    group = group_key("prevision", 2, 10)
    raw = {"sí": 0.9, "no": 0.1}
    f = Forecast(
        id="x",
        question="¿X?",
        distribution=Distribution(outcomes=recal.apply(raw, group)),
        horizon=timedelta(days=10),
        assumptions=[recal.note(group)],
    )
    assert recal.factor(group) != 1.0
    assert raw_of(f)["sí"] == pytest.approx(0.9, abs=1e-6)


def test_grupos():
    assert group_key("practica", 2, 5) == "practica·binaria·corto"
    assert group_key("objetivo", 3, 100) == "objetivo·multiple·medio"
    assert group_key("panel", 2, 365) == "panel·binaria·largo"


# ── preguntas de cantidad ─────────────────────────────────────────────────


def test_intervalos_validos_y_resolver_con_un_numero():
    labels = ["menos de 10", "de 10 a 20", "20 o más"]
    bins = numeric.parse_bins([[None, 10], [10, 20], [20, None]], labels)
    assert bins is not None
    assert numeric.label_for(bins, 9.99) == "menos de 10"
    assert numeric.label_for(bins, 10) == "de 10 a 20"
    assert numeric.label_for(bins, 1e9) == "20 o más"
    # Huecos, desorden o extremos cerrados: no valen.
    assert numeric.parse_bins([[None, 10], [11, 20], [20, None]], labels) is None
    assert numeric.parse_bins([[0, 10], [10, 20], [20, None]], labels) is None
    assert numeric.parse_bins([[None, 10]], labels) is None


@pytest.mark.parametrize(
    ("text", "value"),
    [("1.234,5", 1234.5), ("1,234.5", 1234.5), ("12 %", 12.0), ("3e3", 3000.0), ("7,5", 7.5)],
)
def test_numeros_escritos_de_varias_formas(text, value):
    assert numeric.parse_number(text) == pytest.approx(value)


def test_numero_invalido():
    assert numeric.parse_number("mucho") is None
    assert numeric.parse_number("inf") is None or not math.isfinite(numeric.parse_number("inf"))


def test_prevision_numerica_de_punta_a_punta(env, monkeypatch):  # noqa: F811
    _, service, _, fake = env
    original = fake.chat

    def chat(messages, model, temperature=0.7, json_mode=False, **kwargs):
        if '"outcomes"' in messages[0].content and "Explicas" not in messages[0].content:
            fake.calls += 1
            out = {
                "outcomes": {"menos de 10": 0.2, "de 10 a 20": 0.5, "20 o más": 0.3},
                "numeric": {"unit": "€", "bins": [[None, 10], [10, 20], [20, None]]},
                "assumptions": [],
                "rationale": "r",
            }
            return LLMResponse(content=json.dumps(out), model=model)
        return original(messages, model, temperature, json_mode, **kwargs)

    monkeypatch.setattr(fake, "chat", chat)
    result = service.ask_forecast("¿Cuánto costará?", 30, research=False)
    assert result["forecast"]["numeric"]["unit"] == "€"
    resolved = service.resolve(result["forecast"]["id"], "15,5")
    assert resolved[0]["actual_outcome"] == "de 10 a 20"
