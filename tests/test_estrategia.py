"""Estrategia: puntos de ruptura, valor de la información, rival, pre-mortem y palancas."""

from __future__ import annotations

import json

import pytest

from oraqlo.casefile import CaseError, case_to_dict, parse_case
from oraqlo.drafter import draft_case
from oraqlo.goals import Lever, rank_levers
from oraqlo.llm.base import LLMProvider, LLMResponse
from oraqlo.panel import ROLE_PROMPTS, RoleConfig, SequentialPanel
from oraqlo.simulator.base import Action
from oraqlo.strategy.planner import Recommendation
from oraqlo.strategy.sensitivity import analyze, effective_outcomes
from test_api import env  # noqa: F401  (fixture compartida)

# Caso de juguete calculado a mano (neutral al riesgo, sin ruina):
#   A: 60% bien (+1), 40% mal (−1) → EU 0,2
#   B: seguro «regular» (+0,1)      → EU 0,1
TOY = {
    "question": "¿A o B?",
    "horizon_days": 30,
    "actions": {
        "a": {"description": "Arriesgar", "outcomes": {"bien": 0.6, "mal": 0.4}},
        "b": {"description": "Ir a lo seguro", "outcomes": {"regular": 1.0}},
    },
    "utilities": {"bien": 1.0, "mal": -1.0, "regular": 0.1},
    "risk": {"risk_aversion": 0.0},
}


def test_punto_de_ruptura_de_las_probabilidades():
    result = analyze(parse_case(TOY))
    assert result["best"] == "a"
    # A deja de ganar cuando 2p − 1 < 0,1, es decir, P(bien) < 55%.
    flip = next(
        x
        for x in result["probability"]
        if x["action"] == "a" and x["outcome"] == "bien" and x["direction"] == "baja"
    )
    assert flip["threshold"] == pytest.approx(0.55, abs=1e-3)
    assert flip["new_best"] == "b"
    assert "bajara de 55%" in result["summary"][0]
    assert result["summary"][0].endswith("convendría más «Ir a lo seguro».")
    # La rival que podría ganar: se dice que pasaría a ser la mejor, no «convendría más» ella misma.
    rise = {"action": "b", "action_text": "Ir a lo seguro", "outcome": "regular", "p": 0.5,
            "threshold": 0.7, "direction": "sube", "new_best": "b"}  # fmt: skip
    from oraqlo.strategy.sensitivity import summarize

    names = {"a": "Arriesgar", "b": "Ir a lo seguro"}
    assert "pasaría a ser la mejor opción" in summarize(names, [rise], [])[0]


def test_punto_de_ruptura_de_las_utilidades():
    result = analyze(parse_case(TOY))
    regular = next(x for x in result["utility"] if x["outcome"] == "regular")
    # B gana cuando su resultado seguro vale más que la EU de A (0,2).
    assert regular["threshold"] == pytest.approx(0.2, abs=1e-3)
    assert regular["direction"] == "sube"


def test_valor_de_la_informacion_a_mano():
    result = analyze(parse_case(TOY))
    # Saber de antemano cómo sale A: 0,6·1 + 0,4·0,1 − 0,2 = 0,44. Saber lo de B no vale nada.
    assert [v["action"] for v in result["voi"]] == ["a"]
    assert result["voi"][0]["value"] == pytest.approx(0.44)
    assert "Lo que más vale averiguar" in result["summary"][-1]


def test_con_ruina_se_respeta_el_descarte():
    case = {
        **TOY,
        "risk": {"risk_aversion": 0.0, "ruin_threshold": -0.5, "max_ruin_probability": 0.1},
    }
    result = analyze(parse_case(case))
    assert result["best"] == "b"  # A tiene 40% de «mal» (ruina): fuera
    assert [r["action"] for r in result["ranking"]] == ["b"]


RIVAL_CASE = {
    "question": "¿Bajo precios?",
    "horizon_days": 90,
    "actions": {
        "bajar": {"description": "Bajar precios", "outcomes": {"gano": 0.6, "pierdo": 0.4}},
        "mantener": {"description": "Mantener", "outcomes": {"gano": 0.4, "pierdo": 0.6}},
    },
    "utilities": {"gano": 1.0, "pierdo": -0.5},
    "rival": {
        "description": "la competencia",
        "temperature": 0.05,
        "responses": {
            "iguala": {"description": "Iguala precios"},
            "ignora": {"description": "No hace nada"},
        },
        "reactions": {
            "bajar": {
                "iguala": {"their_utility": 0.5, "outcomes": {"gano": 0.3, "pierdo": 0.7}},
                "ignora": {"their_utility": -0.5, "outcomes": {"gano": 0.8, "pierdo": 0.2}},
            },
            "mantener": {
                "iguala": {"their_utility": -0.2, "outcomes": {"gano": 0.4, "pierdo": 0.6}},
                "ignora": {"their_utility": 0.2, "outcomes": {"gano": 0.4, "pierdo": 0.6}},
            },
        },
    },
}


def test_el_rival_responde_a_cada_jugada():
    spec = parse_case(RIVAL_CASE)
    eff = effective_outcomes(spec)
    # Si bajo precios, el rival casi seguro iguala (es lo mejor para él): gano poco.
    assert eff["bajar"]["gano"] == pytest.approx(0.3, abs=0.01)
    responses = spec.rival.likely_responses(spec.actions[0])
    assert max(responses, key=lambda x: x[1])[0].id == "iguala"
    # Ida y vuelta por el formato de archivo.
    again = parse_case(case_to_dict(spec))
    assert again.rival is not None and again.rival.temperature == 0.05
    assert analyze(spec)["best"] == "mantener"


def test_rival_mal_descrito():
    broken = json.loads(json.dumps(RIVAL_CASE))
    del broken["rival"]["reactions"]["mantener"]["ignora"]
    with pytest.raises(CaseError, match=r"rival\.reactions\.mantener\.ignora"):
        parse_case(broken)


class Drafter(LLMProvider):
    def __init__(self, reply: dict):
        self.reply = reply
        self.calls = 0

    def chat(self, messages, model, temperature=0.7, json_mode=False, **kwargs):
        self.calls += 1
        return LLMResponse(content=json.dumps(self.reply), model=model)


def test_el_redactor_sigue_sin_rival_si_lo_describe_mal():
    reply = {
        "actions": TOY["actions"],
        "utilities": TOY["utilities"],
        "rival": {"description": "x", "responses": {"solo-una": {"description": "y"}}},
    }
    llm = Drafter(reply)
    spec = draft_case(llm, "m", "¿A o B?", 30, max_retries=1)
    assert spec.rival is None and llm.calls == 2  # pidió corregirlo una vez


class PanelLLM(LLMProvider):
    def chat(self, messages, model, temperature=0.7, json_mode=False, **kwargs):
        system = messages[0].content
        if "Red Team" in system:
            out = {
                "objections": ["caro"],
                "premortem": [{"cause": "Nadie compró", "signal": "Ventas < 10 el primer mes"}],
            }
        elif "p_exito" in system:
            out = {"p_exito": 0.6, "p_objeciones": {}}
        elif '"decision"' in system:
            out = {
                "decision": "emitir",
                "confidence": 0.6,
                "rationale": "ok",
                "review_triggers": [],
            }
        else:
            return LLMResponse(content="plan", model=model)
        return LLMResponse(content=json.dumps(out), model=model)


def test_pre_mortem_del_red_team():
    llm = PanelLLM()
    panel = SequentialPanel({n: RoleConfig(n, llm, "m") for n in ROLE_PROMPTS})
    candidate = Recommendation(Action("a", "Abrir tienda"), 0.1, 0.1, 0.5)
    verdict = panel.deliberate("contexto", candidate)
    assert verdict.premortem == [{"cause": "Nadie compró", "signal": "Ventas < 10 el primer mes"}]
    assert "PRE-MORTEM" in ROLE_PROMPTS["red_team"]


def test_palancas_por_rendimiento():
    levers = rank_levers(
        [
            Lever("Mudarse", "alto", "alto", uplift=20),  # 20/3 ≈ 6,7
            Lever("Llamar a 5 clientes", "medio", "bajo", uplift=8),  # 8/1 = 8
            Lever("Curso", "medio", "medio"),  # sin cifra: 7/2 = 3,5
        ]
    )
    assert [lv.text for lv in levers] == ["Llamar a 5 clientes", "Mudarse", "Curso"]


def test_analisis_completo_incluye_puntos_de_ruptura(env):  # noqa: F811
    _, service, *_ = env
    result = service.run_case(dict(TOY))
    assert result["analysis"]["best"] in ("a", "b")
    assert "voi" in result["analysis"]
    assert "Qué haría cambiar la recomendación" in result["markdown"]


def test_probabilidades_negativas_no_valen():
    broken = json.loads(json.dumps(RIVAL_CASE))
    broken["rival"]["reactions"]["bajar"]["iguala"]["outcomes"] = {"gano": 1.2, "pierdo": -0.2}
    with pytest.raises(CaseError, match="entre 0 y 1"):
        parse_case(broken)
    plain = json.loads(json.dumps(TOY))
    plain["actions"]["a"]["outcomes"] = {"bien": 1.3, "mal": -0.3}
    with pytest.raises(CaseError, match="entre 0 y 1"):
        parse_case(plain)
