"""Calidad de predicción: acotado, agregación, recalibración aprendida y su uso."""

from __future__ import annotations

import json
import random
from datetime import timedelta

import pytest

from oraqlo.forecaster.aggregate import clamp, clamp_probability, pool_log_odds, power
from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.forecaster.llm import LLMForecaster
from oraqlo.llm.base import LLMProvider, LLMResponse
from oraqlo.memory.calibration import CalibrationLedger
from oraqlo.memory.recalibration import (
    MIN_RESOLVED,
    Recalibration,
    fit,
    for_model,
    llm_source,
    model_of,
    raw_of,
)
from oraqlo.world_model import WorldModel
from test_api import env  # noqa: F401  (fixture compartida)

# ── acotar ───────────────────────────────────────────────────────────────


def test_nunca_cero_ni_cien():
    assert clamp({"sí": 1.0, "no": 0.0}) == pytest.approx({"sí": 0.98, "no": 0.02})
    three = clamp({"a": 1.0, "b": 0.0, "c": 0.0})
    assert min(three.values()) == pytest.approx(0.01)
    assert sum(three.values()) == pytest.approx(1.0)
    assert clamp_probability(1.7) == 0.98 and clamp_probability(-1) == 0.02


def test_acotar_no_toca_lo_razonable():
    assert clamp({"sí": 0.7, "no": 0.3}) == pytest.approx({"sí": 0.7, "no": 0.3})


# ── agregar ──────────────────────────────────────────────────────────────


def test_agregacion_en_log_odds():
    # Muestras que coinciden no se arrastran al 50% como con la media aritmética.
    pooled = pool_log_odds([{"sí": 0.9, "no": 0.1}, {"sí": 0.9, "no": 0.1}])
    assert pooled["sí"] == pytest.approx(0.9)
    # Simétrico: 0.8 y 0.2 se compensan en 0.5.
    assert pool_log_odds([{"sí": 0.8, "no": 0.2}, {"sí": 0.2, "no": 0.8}])["sí"] == pytest.approx(
        0.5
    )
    with pytest.raises(ValueError):
        pool_log_odds([{"sí": 0.5, "no": 0.5}, {"a": 1.0}])


def test_potencia_suaviza_y_concentra():
    p = {"sí": 0.8, "no": 0.2}
    assert power(p, 0.5)["sí"] < 0.8 < power(p, 2.0)["sí"]
    assert power(p, 1.0) == p


# ── recalibración ────────────────────────────────────────────────────────


def _overconfident_history(n: int, seed: int = 7) -> list[tuple[dict[str, float], str]]:
    """Dice 90% pero acierta ~65%: sobreconfiado."""
    rng = random.Random(seed)
    return [({"sí": 0.9, "no": 0.1}, "sí" if rng.random() < 0.65 else "no") for _ in range(n)]


def test_aprende_a_suavizar_un_modelo_sobreconfiado():
    rc = fit(_overconfident_history(60))
    assert rc.active and rc.a < 1.0
    assert rc.brier_after is not None and rc.brier_before is not None
    assert rc.brier_after < rc.brier_before
    assert rc.apply({"sí": 0.9, "no": 0.1})["sí"] < 0.9


def test_con_pocos_datos_no_corrige():
    rc = fit(_overconfident_history(MIN_RESOLVED - 1))
    assert not rc.active and rc.a == 1.0
    assert rc.apply({"sí": 0.9, "no": 0.1}) == {"sí": 0.9, "no": 0.1}
    assert "Aprendiendo" in rc.describe()


def test_bien_calibrado_no_se_toca():
    rng = random.Random(3)
    history = [({"sí": 0.7, "no": 0.3}, "sí" if rng.random() < 0.7 else "no") for _ in range(400)]
    assert abs(fit(history).a - 1.0) <= 0.15


def test_lo_crudo_se_recupera_de_la_nota():
    rc = Recalibration(a=0.6, n=40)
    calibrated = rc.apply({"sí": 0.9, "no": 0.1})
    f = Forecast(
        id="x",
        question="q",
        distribution=Distribution(outcomes=calibrated),
        horizon=timedelta(days=1),
        assumptions=["otro supuesto", rc.note()],
    )
    assert raw_of(f) == pytest.approx({"sí": 0.9, "no": 0.1})


def test_fuentes_por_tipo_y_modelo_con_dos_puntos():
    assert llm_source("gpt-oss:120b-cloud", "prevision") == "llm:gpt-oss:120b-cloud:prevision"
    assert model_of("llm:gpt-oss:120b-cloud:prevision") == "gpt-oss:120b-cloud"
    assert model_of("llm:gpt-oss:120b-cloud:objetivo") == "gpt-oss:120b-cloud"
    assert model_of("llm:gpt-oss:120b-cloud") == "gpt-oss:120b-cloud"  # práctica
    assert model_of("polymarket:mercado") is None


def test_recalibracion_junta_todas_las_fuentes_del_modelo():
    ledger = CalibrationLedger()
    for i, (outcomes, actual) in enumerate(_overconfident_history(40)):
        kind = [None, "prevision", "objetivo"][i % 3]
        ledger.record(
            Forecast(
                id=f"f{i}",
                question="q",
                distribution=Distribution(outcomes=outcomes),
                horizon=timedelta(days=1),
                source=llm_source("m", kind),
            )
        )
        ledger.resolve(f"f{i}", actual)
    rc = for_model(ledger, "m")
    assert rc.n == 40 and rc.active and rc.a < 1.0
    assert for_model(ledger, "otro").n == 0


# ── el oráculo ───────────────────────────────────────────────────────────


class Scripted(LLMProvider):
    def __init__(self, replies: list[dict]) -> None:
        self.replies = replies
        self.calls: list[tuple[list, float]] = []

    def chat(self, messages, model, temperature=0.7, json_mode=False, **kwargs):
        self.calls.append((list(messages), temperature))
        reply = self.replies[min(len(self.calls), len(self.replies)) - 1]
        return LLMResponse(content=json.dumps(reply), model=model)


def test_varias_estimaciones_sobre_los_mismos_escenarios():
    provider = Scripted(
        [
            {
                "base_rate": "3 de cada 10 lanzamientos parecidos",
                "outcomes": {"sale": 0.6, "no sale": 0.4},
                "assumptions": ["a1"],
                "rationale": "r",
            },
            {"outcomes": {"Sale": 0.8, "no  sale": 0.2}},  # mismas etiquetas, otra forma
            {"outcomes": {"otra cosa": 1.0}},  # cambió los escenarios: se descarta
        ]
    )
    progress: list[str] = []
    f = LLMForecaster(provider, model="m", samples=3, on_progress=progress.append).forecast(
        WorldModel().current(), "¿sale?", timedelta(days=30)
    )
    assert len(provider.calls) == 3
    assert progress == ["Estimación 1 de 3", "Estimación 2 de 3", "Estimación 3 de 3"]
    # Las extra piden las mismas etiquetas y con más variedad.
    assert '"sale", "no sale"' in provider.calls[1][0][-1].content
    assert provider.calls[1][1] >= 0.7
    assert set(f.distribution.outcomes) == {"sale", "no sale"}
    assert 0.6 < f.distribution.outcomes["sale"] < 0.8
    assert "Tasa base: 3 de cada 10 lanzamientos parecidos" in f.assumptions
    assert "Agregado de 2 estimaciones independientes del modelo." in f.assumptions


def test_el_oraculo_aplica_la_recalibracion_y_acota():
    provider = Scripted([{"outcomes": {"sí": 1.0, "no": 0.0}}])
    rc = Recalibration(a=0.5, n=50)
    f = LLMForecaster(provider, model="m", recalibration=rc).forecast(
        WorldModel().current(), "¿?", timedelta(days=1)
    )
    assert f.distribution.outcomes["sí"] < 0.98
    assert any(a.startswith("Recalibrado con su historial (factor a=0.50)") for a in f.assumptions)


# ── en los flujos de la app ──────────────────────────────────────────────


def test_prevision_rapida_varias_estimaciones_y_fuente_propia(env):
    _, service, _, fake = env
    logs: list[str] = []
    result = service.ask_forecast(
        "¿Sube?", 30, research=False, emit=lambda t, d: logs.append(d.get("msg", ""))
    )
    assert result["forecast"]["source"].endswith(":prevision")
    assert fake.calls == service.config.forecast.samples == 3
    assert "Estimación 3 de 3" in logs


def test_objetivo_sin_anclaje_y_con_fuente_propia(env, monkeypatch):
    from oraqlo.goals import GoalStrategist

    _, service, *_ = env
    prompts: list[str] = []

    def fake_chat(self, system, user, temperature=0.2):
        prompts.append(user)
        if "p_logro" in system:
            return {"base_rate": "1 de cada 3", "p_logro": 1.0, "assumptions": []}
        if "levers" in system:
            return {"levers": [{"text": "entrenar"}], "external_conditions": []}
        return {
            "summary": "plan",
            "steps": [{"order": 1, "action": "correr", "when": "ya", "why": "base"}],
            "milestones": [],
        }

    monkeypatch.setattr(GoalStrategist, "_chat", fake_chat)
    result = service.achieve_goal("Correr un maratón", 90, research=False)
    sources = {f.source for f in service.store.ledger.open_forecasts}
    assert sources and all(s.endswith(":objetivo") for s in sources)
    assert not any("se estimó en" in text for text in prompts)  # sin la cifra base
    assert result["kind"] == "goal"
    assert all(
        "Tasa base: 1 de cada 3" in f.assumptions for f in service.store.ledger.open_forecasts
    )
    # Nunca certeza: el 100% del modelo queda en 98%.
    assert all(
        f.distribution.outcomes["se logra"] == 0.98 for f in service.store.ledger.open_forecasts
    )


def test_calibracion_expone_la_correccion_por_modelo(env):
    client, service, *_ = env
    for i, (outcomes, actual) in enumerate(_overconfident_history(MIN_RESOLVED)):
        service.store.ledger.record(
            Forecast(
                id=f"f{i}",
                question="q",
                distribution=Distribution(outcomes=outcomes),
                horizon=timedelta(days=1),
                source=llm_source("m", "prevision"),
            )
        )
        service.store.ledger.resolve(f"f{i}", actual)
    rec = client.get("/api/calibration").json()["recalibration"]
    assert rec[0]["model"] == "m" and rec[0]["active"] is True and rec[0]["a"] < 1
