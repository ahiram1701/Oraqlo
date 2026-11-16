"""La respuesta en palabras llanas: con las cifras finales, y sin romper nada si falla."""

from __future__ import annotations

import json
from datetime import timedelta

from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.forecaster.explain import ANSWER_PREFIX, WHY_PREFIX, explain
from oraqlo.llm.base import LLMProvider, LLMResponse
from test_api import env  # noqa: F401  (fixture compartida)


class Fake(LLMProvider):
    def __init__(self, content: str | Exception):
        self.content = content
        self.prompts: list[str] = []

    def chat(self, messages, model, temperature=0.7, json_mode=False, **kwargs):
        self.prompts.append(messages[-1].content)
        if isinstance(self.content, Exception):
            raise self.content
        return LLMResponse(content=self.content, model=model)


def _forecast() -> Forecast:
    return Forecast(
        id="f",
        question="¿Cuándo habrá una crisis mundial?",
        distribution=Distribution(
            outcomes={
                "No antes de 2036": 0.61,
                "En 2032-2036": 0.16,
                "En 2029-2031": 0.14,
                "En 2026-2028": 0.09,
            }
        ),
        horizon=timedelta(days=3650),
        assumptions=[
            "Tasa base: 3,6% anual (Barro-Ursúa)",
            "Razonamiento: riesgos elevados",
            "Otro supuesto",
        ],
        source="llm:m:prevision",
    )


def test_anade_respuesta_y_porque_al_principio_con_las_cifras_finales():
    reply = json.dumps(
        {
            "answer": "Hay un 39% de que ocurra antes de 2036; si ocurre, lo más probable es entre 2032 y 2036.",
            "why": "Las crisis así son raras, pero hoy hay más riesgos de lo normal.",
        }
    )
    fake = Fake(reply)
    f = explain(fake, "m", "¿Cuándo habrá una crisis mundial?", _forecast())
    assert f.assumptions[0].startswith(ANSWER_PREFIX + "Hay un 39%")
    assert f.assumptions[1].startswith(WHY_PREFIX)
    assert f.assumptions[2:] == [
        "Tasa base: 3,6% anual (Barro-Ursúa)",
        "Razonamiento: riesgos elevados",
        "Otro supuesto",
    ]
    # El modelo recibe las probabilidades que verá el usuario y el razonamiento.
    assert "- No antes de 2036: 61%" in fake.prompts[0] and "Razonamiento:" in fake.prompts[0]


def test_explicar_dos_veces_no_duplica():
    fake = Fake(json.dumps({"answer": "A", "why": "B"}))
    f = explain(fake, "m", "¿X?", explain(fake, "m", "¿X?", _forecast()))
    assert sum(a.startswith(ANSWER_PREFIX) for a in f.assumptions) == 1


def test_si_el_modelo_falla_la_prevision_sigue_igual():
    before = list(_forecast().assumptions)
    assert explain(Fake(RuntimeError("caído")), "m", "¿X?", _forecast()).assumptions == before
    assert explain(Fake("no es json"), "m", "¿X?", _forecast()).assumptions == before
    assert (
        explain(Fake(json.dumps({"why": "sin respuesta"})), "m", "¿X?", _forecast()).assumptions
        == before
    )


def test_la_prevision_rapida_guarda_la_respuesta(env):
    _client, service, *_ = env
    result = service.ask_forecast("¿Sube?", 30, research=False)
    assert result["forecast"]["assumptions"][0] == ANSWER_PREFIX + "Probablemente sube: 70%."
    stored = service.store.ledger.open_forecasts[0]
    assert stored.assumptions[0].startswith(ANSWER_PREFIX)
