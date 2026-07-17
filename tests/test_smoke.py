"""Tests de humo: los contratos existen, importan, y se comportan como contratos.

Ejecutar con `python -m pytest tests/ -q` o directamente `python tests/test_smoke.py`.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from oraqlo.connectors.base import Connector
from oraqlo.forecaster.base import Distribution, Forecast, Forecaster
from oraqlo.llm import OllamaBackendConfig, OllamaProvider, OllamaUnavailableError
from oraqlo.llm.base import ChatMessage
from oraqlo.forecaster.llm import ForecastParseError, LLMForecaster
from oraqlo.llm.base import LLMProvider, LLMResponse
from oraqlo.memory.calibration import CalibrationLedger, brier_score, log_loss, multiclass_brier
from oraqlo.panel import ROLE_PROMPTS, DecisionPanel, PanelError, RoleConfig, SequentialPanel
from oraqlo.simulator.base import (
    Action,
    AdversaryModel,
    MonteCarloEngine,
    ScenarioEngine,
    SoftmaxAdversary,
    Trajectory,
    TreeSearchEngine,
)
from oraqlo.strategy.planner import ExpectedUtilityPlanner, Planner, Recommendation, RiskProfile
from oraqlo.world_model import Epistemic, Observation, WorldModel


def test_abcs_no_instanciables():
    """Las interfaces son abstractas: instanciarlas directamente es un error."""
    for abc in (Connector, Forecaster, ScenarioEngine, AdversaryModel, Planner, DecisionPanel):
        with pytest.raises(TypeError):
            abc()  # type: ignore[abstract]


def test_world_model_apply_versiona_sin_mutar():
    wm = WorldModel()
    assert wm.current().version == 0
    v0 = wm.current()

    s1 = wm.apply(Observation(entity_id="e1", variable="x", value=1.0, source="sensor"))
    assert s1.version == 1
    var = s1.entities["e1"].variables["x"]
    assert var.value == 1.0
    assert var.epistemic is Epistemic.KNOWN  # observado directamente
    assert v0.entities == {}  # el estado previo no se mutó

    s2 = wm.apply(Observation(entity_id="e1", variable="x", value=2.0, source="sensor"))
    assert s2.entities["e1"].variables["x"].value == 2.0
    assert s1.entities["e1"].variables["x"].value == 1.0  # inmutabilidad


def test_world_model_at_rebobina():
    from datetime import datetime, timedelta as td, timezone

    wm = WorldModel()
    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    wm.apply(Observation(entity_id="e", variable="x", value="a", source="s", timestamp=t1))
    wm.apply(Observation(entity_id="e", variable="x", value="b", source="s", timestamp=t2))

    assert wm.at(t1 + td(days=30)).entities["e"].variables["x"].value == "a"
    assert wm.at(t2).entities["e"].variables["x"].value == "b"
    assert wm.at(datetime(2020, 1, 1, tzinfo=timezone.utc)).version == 0


def test_static_connector_entrega_una_vez():
    from oraqlo.connectors import StaticConnector

    conn = StaticConnector("demo", [Observation(entity_id="e", variable="x", value=1, source="demo")])
    assert len(list(conn.poll())) == 1
    assert list(conn.poll()) == []


def test_distribution_validate():
    Distribution(outcomes={"a": 0.7, "b": 0.3}).validate()
    with pytest.raises(ValueError):
        Distribution(outcomes={"a": 0.7, "b": 0.7}).validate()
    with pytest.raises(ValueError):
        Distribution(interval=(5.0, 1.0)).validate()


def test_brier_y_logloss():
    assert brier_score(1.0, True) == 0.0
    assert brier_score(0.0, True) == 1.0
    assert brier_score(0.5, True) == pytest.approx(0.25)
    assert log_loss(0.5, True) == pytest.approx(0.6931, abs=1e-3)
    with pytest.raises(ValueError):
        brier_score(1.5, True)


def _forecast(fid="f1", outcomes=None, source="test") -> Forecast:
    return Forecast(
        id=fid,
        question="¿sube?",
        distribution=Distribution(outcomes=outcomes or {"sí": 0.6, "no": 0.4}),
        horizon=timedelta(days=30),
        source=source,
    )


def test_multiclass_brier():
    # Predicción perfecta: 0. Certeza en el equivocado: 2. Outcome no contemplado: peor.
    assert multiclass_brier({"a": 1.0, "b": 0.0}, "a") == 0.0
    assert multiclass_brier({"a": 1.0, "b": 0.0}, "b") == 2.0
    assert multiclass_brier({"a": 0.6, "b": 0.4}, "a") == pytest.approx(0.32)
    assert multiclass_brier({"a": 0.5, "b": 0.5}, "c") == pytest.approx(1.5)


def test_ledger_resolve_y_reliability():
    ledger = CalibrationLedger()
    ledger.record(_forecast("f1", {"sí": 0.6, "no": 0.4}))
    ledger.record(_forecast("f2", {"sí": 0.9, "no": 0.1}))

    r1 = ledger.resolve("f1", "sí")
    assert r1.brier == pytest.approx(0.32)
    assert len(ledger.open_forecasts) == 1

    r2 = ledger.resolve("f2", "no")  # sobreconfianza castigada
    assert r2.brier == pytest.approx(1.62)

    # Fiabilidad de la fuente = Brier medio; fuente desconocida = None.
    assert ledger.reliability("test") == pytest.approx((0.32 + 1.62) / 2)
    assert ledger.reliability("otra") is None

    # Resolver dos veces (o un id inexistente) es un error explícito.
    with pytest.raises(KeyError):
        ledger.resolve("f1", "sí")


def test_ledger_discard_y_delete_resolved():
    ledger = CalibrationLedger()
    ledger.record(_forecast("f1"))
    ledger.record(_forecast("f2"))

    # Descartar una abierta la quita sin puntuarla.
    ledger.discard("f1")
    assert [f.id for f in ledger.open_forecasts] == ["f2"]
    assert ledger.resolved_forecasts == []
    with pytest.raises(KeyError):
        ledger.discard("f1")

    # Borrar una resuelta recalcula la fiabilidad sin ella.
    ledger.resolve("f2", "sí")
    assert ledger.reliability("test") is not None
    ledger.delete_resolved("f2")
    assert ledger.resolved_forecasts == []
    assert ledger.reliability("test") is None
    with pytest.raises(KeyError):
        ledger.delete_resolved("f2")


class FakeProvider(LLMProvider):
    """Doble de test: devuelve respuestas fijadas, registra las llamadas."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[list] = []

    def chat(self, messages, model, temperature=0.7, json_mode=False) -> LLMResponse:
        self.calls.append(list(messages))
        return LLMResponse(content=self.responses[len(self.calls) - 1], model=model)


def test_llm_forecaster_parsea_y_normaliza():
    provider = FakeProvider(
        ['{"outcomes": {"éxito": 0.59, "fracaso": 0.40}, '
         '"assumptions": ["equipo estable"], "rationale": "histórico similar"}']
    )
    fc = LLMForecaster(provider, model="fake")
    f = fc.forecast(WorldModel().current(), "¿migramos?", timedelta(days=90))
    # 0.99 se normaliza a 1.0 exacto (perdona redondeo, no incoherencia).
    assert sum(f.distribution.outcomes.values()) == pytest.approx(1.0)
    assert f.source == "llm:fake"
    assert any("equipo estable" in a for a in f.assumptions)


def test_llm_forecaster_reintenta_con_feedback():
    provider = FakeProvider(
        ["esto no es JSON",
         '{"outcomes": {"a": 0.5, "b": 0.5}, "assumptions": [], "rationale": ""}']
    )
    fc = LLMForecaster(provider, model="fake", max_retries=1)
    f = fc.forecast(WorldModel().current(), "¿a o b?", timedelta(days=7))
    assert f.distribution.outcomes == {"a": 0.5, "b": 0.5}
    # El segundo intento incluye el feedback del error en la conversación.
    assert len(provider.calls) == 2
    assert "no es válida" in provider.calls[1][-1].content


def test_llm_forecaster_falla_claro_si_no_hay_json():
    provider = FakeProvider(["nada", "tampoco"])
    fc = LLMForecaster(provider, model="fake", max_retries=1)
    with pytest.raises(ForecastParseError):
        fc.forecast(WorldModel().current(), "¿?", timedelta(days=1))


def test_llm_forecaster_rechaza_probabilidades_incoherentes():
    # Suma 0.5: demasiado lejos de 1.0 para perdonarlo como redondeo.
    provider = FakeProvider(
        ['{"outcomes": {"a": 0.3, "b": 0.2}, "assumptions": [], "rationale": ""}'] * 2
    )
    fc = LLMForecaster(provider, model="fake", max_retries=1)
    with pytest.raises(ForecastParseError):
        fc.forecast(WorldModel().current(), "¿?", timedelta(days=1))


def test_panel_exige_todos_los_roles():
    class DummyPanel(DecisionPanel):
        def deliberate(self, context, candidate):
            raise NotImplementedError

    with pytest.raises(ValueError):
        DummyPanel(roles={})
    assert set(ROLE_PROMPTS) == {"estratega", "red_team", "oraculo", "juez"}


def test_ollama_cloud_sin_api_key_da_error_claro(monkeypatch):
    """El backend cloud sin OLLAMA_API_KEY debe fallar con mensaje accionable."""
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    provider = OllamaProvider(OllamaBackendConfig.cloud())
    with pytest.raises(OllamaUnavailableError, match="OLLAMA_API_KEY"):
        provider.chat([ChatMessage(role="user", content="hola")], model="gpt-oss:120b-cloud")


def _candidate() -> "Recommendation":
    from oraqlo.simulator.base import Action
    from oraqlo.strategy.planner import Recommendation

    return Recommendation(
        action=Action(id="a1", description="lanzar en beta cerrada"),
        expected_utility=0.7,
        risk_adjusted_utility=0.6,
        confidence=0.5,
        assumptions=["mercado receptivo"],
    )


def _panel(provider) -> SequentialPanel:
    roles = {
        name: RoleConfig(name=name, provider=provider, model="fake", temperature=0.1)
        for name in ROLE_PROMPTS
    }
    return SequentialPanel(roles)


def test_panel_debate_completo():
    provider = FakeProvider([
        "Tesis: la beta cerrada valida demanda con riesgo acotado. Supuestos: hay early adopters.",
        '{"objections": ["los early adopters no representan al mercado", "el feedback llegará tarde"]}',
        '{"p_exito": 0.6, "p_objeciones": {"los early adopters no representan al mercado": 0.4, "el feedback llegará tarde": 0.3}}',
        '{"decision": "emitir", "confidence": 0.55, "rationale": "riesgo acotado y reversible", '
        '"review_triggers": ["menos de 20 usuarios activos en 2 semanas"]}',
    ])
    verdict = _panel(provider).deliberate("startup pre-lanzamiento", _candidate())

    assert verdict.decision == "emitir"
    assert verdict.confidence == pytest.approx(0.55)
    assert verdict.p_success == pytest.approx(0.6)
    assert len(verdict.objections) == 2
    assert verdict.review_triggers == ["menos de 20 usuarios activos en 2 semanas"]
    # Trazabilidad: el transcript conserva los 4 turnos en orden de debate.
    assert [rol for rol, _ in verdict.transcript] == ["estratega", "red_team", "oraculo", "juez"]


def test_panel_sin_objeciones_es_invalido():
    """Un Red Team que no ataca invalida el debate: la auto-crítica es obligatoria."""
    provider = FakeProvider([
        "Plan perfecto.",
        '{"objections": []}',  # JSON válido pero sin ataque: se rechaza sin reintento
    ])
    with pytest.raises(PanelError, match="objeciones"):
        _panel(provider).deliberate("ctx", _candidate())


def test_panel_juez_decision_invalida():
    provider = FakeProvider([
        "Plan.",
        '{"objections": ["algo"]}',
        '{"p_exito": 0.5, "p_objeciones": {}}',
        '{"decision": "quizás", "confidence": 0.5, "rationale": "", "review_triggers": []}',
    ])
    with pytest.raises(PanelError, match="inválida"):
        _panel(provider).deliberate("ctx", _candidate())


def test_panel_reintenta_json_roto():
    provider = FakeProvider([
        "Plan.",
        "esto no es JSON",  # primer intento del red team falla...
        '{"objections": ["ataque"]}',  # ...y el reintento con feedback lo corrige
        '{"p_exito": 0.5, "p_objeciones": {"ataque": 0.2}}',
        '{"decision": "revisar", "confidence": 0.4, "rationale": "dudas", "review_triggers": []}',
    ])
    verdict = _panel(provider).deliberate("ctx", _candidate())
    assert verdict.decision == "revisar"
    assert verdict.objections == ["ataque"]


def test_panel_on_turn_recibe_los_turnos_en_orden():
    provider = FakeProvider([
        "Plan.",
        '{"objections": ["algo"]}',
        '{"p_exito": 0.5, "p_objeciones": {}}',
        '{"decision": "emitir", "confidence": 0.5, "rationale": "", "review_triggers": []}',
    ])
    turns: list[str] = []
    verdict = _panel(provider).deliberate(
        "ctx", _candidate(), on_turn=lambda role, text: turns.append(role)
    )
    assert turns == ["estratega", "red_team", "oraculo", "juez"]
    # El transcript y el streaming ven exactamente lo mismo.
    assert [rol for rol, _ in verdict.transcript] == turns


def test_drafter_genera_caso_valido():
    from oraqlo.drafter import draft_case

    provider = FakeProvider([
        '{"actions": {"lanzar": {"description": "Lanzar ya", '
        '"outcomes": {"éxito": 0.4, "fracaso": 0.6}}, '
        '"esperar": {"description": "Esperar un trimestre", '
        '"outcomes": {"mercado madura": 0.5, "competidor se adelanta": 0.5}}}, '
        '"utilities": {"éxito": 1.0, "fracaso": -0.6, "mercado madura": 0.3, '
        '"competidor se adelanta": -0.4}}'
    ])
    spec = draft_case(provider, "fake", "¿lanzamos el producto?", horizon_days=120)
    assert spec.question == "¿lanzamos el producto?"
    assert {a.id for a in spec.actions} == {"lanzar", "esperar"}
    assert spec.horizon.days == 120
    assert spec.risk.ruin_threshold == -0.9  # riesgo por defecto conservador


def test_drafter_reintenta_y_falla_claro():
    from oraqlo.drafter import DraftError, draft_case

    # Probabilidades que no suman 1.0 en todos los intentos → error accionable.
    bad = ('{"actions": {"a": {"description": "A", "outcomes": {"x": 0.5, "y": 0.2}}}, '
           '"utilities": {"x": 1.0, "y": 0.0}}')
    provider = FakeProvider([bad, bad, bad])
    with pytest.raises(DraftError, match="no produjo un caso válido"):
        draft_case(provider, "fake", "¿?", horizon_days=30, max_retries=2)
    # El feedback del error viajó en la conversación de reintento.
    assert "no es válido" in provider.calls[1][-1].content

    with pytest.raises(DraftError, match="vacía"):
        draft_case(provider, "fake", "   ", horizon_days=30)


def test_web_client_sin_api_key(monkeypatch):
    from oraqlo.llm.ollama import OllamaUnavailableError
    from oraqlo.llm.websearch import OllamaWebClient

    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    client = OllamaWebClient()
    assert client.is_available() is False
    with pytest.raises(OllamaUnavailableError, match="OLLAMA_API_KEY"):
        client.search("algo")


def test_web_client_parsea_resultados(monkeypatch):
    from oraqlo.llm.websearch import OllamaWebClient

    client = OllamaWebClient()
    monkeypatch.setattr(
        client, "_post",
        lambda path, payload: {"results": [
            {"title": "T1", "url": "https://a.example", "content": "C1"},
            {"url": "https://b.example", "content": "C2"},
            {"title": "sin url"},  # descartado
        ]},
    )
    results = client.search("q", max_results=5)
    assert [(r.title, r.url) for r in results] == [("T1", "https://a.example"),
                                                   ("", "https://b.example")]
    monkeypatch.setattr(client, "_post",
                        lambda path, payload: {"title": "Página", "content": "x" * 10000})
    text = client.fetch("https://a.example", max_chars=100)
    assert text.startswith("Página") and len(text) == 100


class FakeWebClient:
    """Doble del buscador: resultados fijos, registra las consultas."""

    def __init__(self, results=None):
        self.queries: list[str] = []
        self.results = results if results is not None else [
            type("R", (), {"title": "Informe 2026", "url": "https://x.example/informe",
                           "snippet": "El mercado creció 12% en 2025."})()
        ]

    def is_available(self):
        return True

    def search(self, query, max_results=5):
        self.queries.append(query)
        return self.results

    def fetch(self, url, max_chars=6000):
        return "contenido"


def test_researcher_produce_brief_y_observaciones():
    from oraqlo.forecaster.llm import _summarize_state
    from oraqlo.research import BRIEF_HEADER, Researcher

    provider = FakeProvider([
        '{"queries": ["mercado apps 2026", "competidores recetas"]}',
        '{"action": "synthesize"}',  # la reflexión decide que basta una ronda
        '{"facts": [{"text": "El mercado creció 12% en 2025", '
        '"source": "https://x.example/informe"}]}',
    ])
    web = FakeWebClient()
    brief = Researcher(web, provider, model="fake").investigate("¿lanzamos?", timedelta(days=90))

    assert brief.queries == ["mercado apps 2026", "competidores recetas"]
    assert web.queries == brief.queries  # cada consulta se buscó de verdad
    assert brief.facts[0].source_url == "https://x.example/informe"
    assert BRIEF_HEADER in brief.as_context()

    # Las observaciones entran al WorldModel y llegan al prompt del forecaster.
    wm = WorldModel()
    for obs in brief.as_observations():
        wm.apply(obs)
    summary = _summarize_state(wm.current())
    assert "El mercado creció 12% en 2025" in summary
    assert "https://x.example/informe" in summary


def test_researcher_itera_cuando_falta_informacion():
    from oraqlo.research import Researcher

    provider = FakeProvider([
        '{"queries": ["consulta inicial"]}',
        # Reflexión 1: falta un dato → busca más.
        '{"action": "search", "queries": ["dato que faltaba"], "reason": "falta el precio"}',
        # Reflexión 2: suficiente.
        '{"action": "synthesize"}',
        '{"facts": [{"text": "Hecho completo", "source": "https://x.example/informe"}]}',
    ])
    web = FakeWebClient()
    progress: list[str] = []
    brief = Researcher(web, provider, model="fake", max_rounds=3).investigate(
        "¿?", timedelta(days=30), on_progress=progress.append
    )
    assert web.queries == ["consulta inicial", "dato que faltaba"]
    assert brief.rounds == 2
    assert brief.queries == ["consulta inicial", "dato que faltaba"]
    # El progreso narró la segunda ronda con su motivo.
    assert any("ronda 2" in p and "falta el precio" in p for p in progress)


def test_researcher_fetch_solo_urls_vistas():
    from oraqlo.research import Researcher

    provider = FakeProvider([
        '{"queries": ["q"]}',
        # Pide leer una URL vista y una inventada: solo la vista se lee.
        '{"action": "fetch", "urls": ["https://x.example/informe", '
        '"https://atacante.example/mal"], "reason": "detalle"}',
        '{"action": "synthesize"}',
        '{"facts": [{"text": "H", "source": "https://x.example/informe"}]}',
    ])
    web = FakeWebClient()
    fetched: list[str] = []
    web.fetch = lambda url, max_chars=6000: (fetched.append(url), "contenido")[1]
    brief = Researcher(web, provider, model="fake", max_rounds=3).investigate(
        "¿?", timedelta(days=30)
    )
    assert fetched == ["https://x.example/informe"]  # la URL inventada NO se visitó
    assert brief.fetched_urls == ["https://x.example/informe"]


def test_researcher_respeta_el_tope_de_rondas():
    from oraqlo.research import Researcher

    # La reflexión pide más búsquedas; el tope corta y pasa directo a síntesis.
    provider = FakeProvider([
        '{"queries": ["q1"]}',
        '{"action": "search", "queries": ["q2"], "reason": "más"}',
        '{"facts": [{"text": "H", "source": "https://x.example/informe"}]}',
    ])
    web = FakeWebClient()
    brief = Researcher(web, provider, model="fake", max_rounds=2).investigate(
        "¿?", timedelta(days=30)
    )
    assert brief.rounds == 2  # tope respetado
    assert web.queries == ["q1", "q2"]
    # Con max_rounds=2 solo hubo UNA reflexión: la síntesis usó la 3ª respuesta.
    assert len(provider.calls) == 3


def test_researcher_falla_claro_sin_resultados():
    from oraqlo.research import Researcher, ResearchError

    provider = FakeProvider(['{"queries": ["algo"]}'])
    web = FakeWebClient(results=[])
    with pytest.raises(ResearchError, match="ningún resultado"):
        Researcher(web, provider, model="fake").investigate("¿?", timedelta(days=30))


def test_chat_json_reintenta_y_falla_claro():
    from oraqlo.llm.jsonchat import JsonChatError, chat_json

    ok = FakeProvider(["basura", '{"a": 1}'])
    assert chat_json(ok, "fake", "sys", "user") == {"a": 1}
    assert "no es válida" in ok.calls[1][-1].content  # feedback del error

    bad = FakeProvider(["basura", "más basura"])
    with pytest.raises(JsonChatError, match="no produjo JSON válido"):
        chat_json(bad, "fake", "sys", "user")


# ── Modo objetivo ─────────────────────────────────────────────────────────

GOAL_PANEL_OK = [
    "Plan articulado.",
    '{"objections": ["puede faltar constancia"]}',
    '{"p_exito": 0.6, "p_objeciones": {"puede faltar constancia": 0.4}}',
    '{"decision": "emitir", "confidence": 0.6, "rationale": "razonable", "review_triggers": []}',
]


def _goal_responses(baseline_p=0.2, with_plan_p=0.6, panel=None):
    return [
        # 1. línea base
        f'{{"p_logro": {baseline_p}, "assumptions": ["sigues igual"], "rationale": "statu quo"}}',
        # 2. palancas
        '{"levers": [{"text": "entrenar 4 días/semana", "impact": "alto", "effort": "medio"}],'
        ' "external_conditions": [{"text": "no lesionarte", "probability": 0.8}]}',
        # 3. plan
        '{"summary": "Plan progresivo", "steps": [{"order": 1, "action": "base aeróbica",'
        ' "when": "mes 1-2", "why": "resistencia"}], "milestones":'
        ' [{"text": "media maratón en 1h55", "target_date": "2026-10-01", "signal": "ritmo"}]}',
        # 4. panel
        *(panel or GOAL_PANEL_OK),
        # 5. condicional
        f'{{"p_logro": {with_plan_p}, "assumptions": [], "rationale": "con plan"}}',
    ]


def _pursue(provider):
    from oraqlo.goals import GoalStrategist

    ledger = CalibrationLedger()
    plan = GoalStrategist(provider, model="fake").pursue(
        "correr maratón sub-4h", timedelta(days=180), _panel(provider), ledger
    )
    return plan, ledger


def test_goal_pipeline_completo():
    provider = FakeProvider(_goal_responses())
    plan, ledger = _pursue(provider)

    assert plan.baseline_p == pytest.approx(0.2)
    assert plan.with_plan_p == pytest.approx(0.6)
    assert plan.uplift == pytest.approx(0.4)
    assert plan.levers[0].impact == "alto"
    assert plan.external_conditions[0].probability == pytest.approx(0.8)
    assert plan.steps[0].action == "base aeróbica"
    assert plan.milestones[0].target_date == "2026-10-01"
    assert plan.verdict.decision == "emitir"
    assert plan.revised is False
    # Las DOS predicciones quedaron abiertas en el ledger, etiquetadas.
    questions = sorted(f.question for f in ledger.open_forecasts)
    assert questions == ["[con plan] correr maratón sub-4h", "[statu quo] correr maratón sub-4h"]


def test_goal_revisar_rehace_el_plan():
    panel_revisar = [
        "Plan v1.",
        '{"objections": ["hitos poco realistas"]}',
        '{"p_exito": 0.4, "p_objeciones": {"hitos poco realistas": 0.6}}',
        '{"decision": "revisar", "confidence": 0.5, "rationale": "flojo", "review_triggers": []}',
    ]
    plan_v2 = ('{"summary": "Plan v2 realista", "steps": [{"order": 1, "action": "mejor paso",'
               ' "when": "ya", "why": "objeciones atendidas"}], "milestones": []}')
    provider = FakeProvider([
        *_goal_responses(panel=panel_revisar)[:-1],  # hasta el primer debate (sin condicional)
        plan_v2,
        *GOAL_PANEL_OK,  # segundo debate: emitir
        '{"p_logro": 0.55, "assumptions": [], "rationale": ""}',
    ])
    plan, _ = _pursue(provider)
    assert plan.revised is True
    assert plan.summary == "Plan v2 realista"
    assert plan.verdict.decision == "emitir"
    # El re-diseño vio las objeciones del primer debate.
    replan_prompt = provider.calls[7][-1].content
    assert "hitos poco realistas" in replan_prompt


def test_goal_uplift_negativo_se_reporta_sin_maquillar():
    provider = FakeProvider(_goal_responses(baseline_p=0.5, with_plan_p=0.35))
    plan, _ = _pursue(provider)
    assert plan.uplift == pytest.approx(-0.15)


def test_goal_sin_palancas_es_error():
    from oraqlo.goals import GoalError

    responses = _goal_responses()
    responses[1] = '{"levers": [], "external_conditions": []}'
    provider = FakeProvider(responses)
    with pytest.raises(GoalError, match="palanca"):
        _pursue(provider)


def test_drafter_incluye_contexto_de_investigacion():
    from oraqlo.drafter import draft_case

    provider = FakeProvider([
        '{"actions": {"a": {"description": "A", "outcomes": {"x": 1.0}}}, '
        '"utilities": {"x": 0.5}}'
    ])
    draft_case(provider, "fake", "¿?", horizon_days=30,
               context="HECHOS RECUPERADOS: el mercado creció 12%")
    assert "el mercado creció 12%" in provider.calls[0][-1].content


# ── Polymarket + práctica autónoma ────────────────────────────────────────


def _market_payload(mid="111", question="¿Pasará X?", prices=("0.35", "0.65"),
                    closed=False, uma=None, end="2026-07-25T00:00:00Z", vol=50000.0):
    return {"id": mid, "question": question, "outcomes": '["Yes", "No"]',
            "outcomePrices": f'["{prices[0]}", "{prices[1]}"]', "closed": closed,
            "umaResolutionStatus": uma, "endDate": end, "volume24hr": vol}


def test_polymarket_parsea_abiertos_y_resueltos(monkeypatch):
    from oraqlo.polymarket import PolymarketClient

    client = PolymarketClient()
    monkeypatch.setattr(client, "_get", lambda path, params=None: [
        _market_payload(mid="1"),
        _market_payload(mid="2", vol=100.0),                      # volumen bajo: fuera
        _market_payload(mid="3", end="2027-01-01T00:00:00Z"),     # cierra tarde: fuera
        _market_payload(mid="4", closed=True),                    # cerrado: fuera
    ])
    markets = client.list_open(limit=10, min_volume_24h=10000, max_days_to_close=30)
    assert [m.id for m in markets] == ["1"]
    assert markets[0].outcomes == {"Yes": 0.35, "No": 0.65}

    monkeypatch.setattr(client, "_get", lambda path, params=None:
                        _market_payload(mid="9", prices=("0", "1"), closed=True, uma="resolved"))
    resolved = client.get_market("9")
    assert resolved.resolved and resolved.winner == "No"


class FakeMarketClient:
    def __init__(self, open_markets=None, by_id=None):
        self.open_markets = open_markets or []
        self.by_id = by_id or {}

    def list_open(self, limit=20, **kw):
        return self.open_markets[:limit]

    def get_market(self, market_id):
        return self.by_id[market_id]


def _mk(mid, question="¿Pasará X?", yes=0.35, resolved=False, winner=None):
    from datetime import datetime, timezone
    from oraqlo.polymarket import Market

    return Market(id=mid, question=question, outcomes={"Yes": yes, "No": 1 - yes},
                  end_date=datetime(2026, 7, 25, tzinfo=timezone.utc),
                  closed=resolved, resolved=resolved, winner=winner)


def test_practice_predice_con_benchmark_y_dedupe():
    from oraqlo.autopractice import MARKET_SOURCE, PracticeEngine

    provider = FakeProvider([
        '{"outcomes": {"Yes": 0.6, "No": 0.4}, "assumptions": ["a1"]}',
    ])
    ledger = CalibrationLedger()
    engine = PracticeEngine(FakeMarketClient([_mk("77")]), provider, "fake", ledger)

    predicted = engine.predict_new(n=1, research=False)
    assert len(predicted) == 1
    # Dos predicciones por mercado: la de Oraqlo y el precio del mercado (benchmark).
    sources = sorted(f.source for f in ledger.open_forecasts)
    assert sources == ["llm:fake", MARKET_SOURCE]
    assert all(f.question.startswith("[polymarket:77]") for f in ledger.open_forecasts)
    bench = next(f for f in ledger.open_forecasts if f.source == MARKET_SOURCE)
    assert bench.distribution.outcomes["Yes"] == pytest.approx(0.35)
    # El prompt no revela el precio del mercado y exige las etiquetas exactas.
    prompt = provider.calls[0][-1].content
    assert "0.35" not in prompt and "['Yes', 'No']" in prompt

    # Dedupe: el mismo mercado no se predice dos veces.
    assert engine.predict_new(n=1, research=False) == []


def test_practice_resuelve_ambas_con_el_ganador():
    from oraqlo.autopractice import PracticeEngine

    provider = FakeProvider(['{"outcomes": {"Yes": 0.8, "No": 0.2}, "assumptions": []}'])
    ledger = CalibrationLedger()
    client = FakeMarketClient([_mk("55")])
    engine = PracticeEngine(client, provider, "fake", ledger)
    engine.predict_new(n=1, research=False)

    client.by_id["55"] = _mk("55", resolved=True, winner="No")
    resolved = engine.resolve_due()
    assert len(resolved) == 2 and ledger.open_forecasts == []
    briers = {r.forecast.source: r.brier for r in resolved}
    # Oraqlo dijo 80% Yes y salió No: castigo fuerte. El mercado (35% Yes) sufre menos.
    assert briers["llm:fake"] == pytest.approx(1.28)
    assert briers["polymarket:mercado"] == pytest.approx(2 * 0.35**2)


def test_calibration_advice_detecta_sobreconfianza():
    from oraqlo.autopractice import calibration_advice

    ledger = CalibrationLedger()
    # 4 resueltas confiadas (80% al outcome equivocado la mitad de las veces).
    for i, actual in enumerate(["si", "no", "no", "no"]):
        ledger.record(_forecast(f"c{i}", {"si": 0.8, "no": 0.2}, source="llm:fake"))
        ledger.resolve(f"c{i}", actual)
    advice = calibration_advice(ledger, "llm:fake")
    assert advice is not None
    assert "SOBRECONFÍAS" in advice

    # Con menos de 3 resueltas no hay consejo (evita sobreajustar al ruido).
    assert calibration_advice(CalibrationLedger(), "llm:fake") is None


def test_practice_advice_llega_al_prompt():
    from oraqlo.autopractice import PracticeEngine

    ledger = CalibrationLedger()
    for i, actual in enumerate(["no", "no", "no"]):
        ledger.record(_forecast(f"h{i}", {"si": 0.9, "no": 0.1}, source="llm:fake"))
        ledger.resolve(f"h{i}", actual)
    provider = FakeProvider(['{"outcomes": {"Yes": 0.5, "No": 0.5}, "assumptions": []}'])
    engine = PracticeEngine(FakeMarketClient([_mk("88")]), provider, "fake", ledger)
    engine.predict_new(n=1, research=False)
    system_prompt = provider.calls[0][0].content
    assert "historial de calibración" in system_prompt
    assert "SOBRECONFÍAS" in system_prompt


def test_autopractice_cli_help():
    from oraqlo.autopractice import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_ollama_list_models(monkeypatch):
    from oraqlo.llm.ollama import OllamaProvider

    provider = OllamaProvider()
    monkeypatch.setattr(
        provider,
        "_get_json",
        lambda path, timeout_s=5.0: {"models": [{"name": "qwen2.5"}, {"name": "llama3.1"}]},
    )
    assert provider.list_models() == ["llama3.1", "qwen2.5"]  # ordenados


def test_panel_confianza_se_recorta_a_rango():
    provider = FakeProvider([
        "Plan.",
        '{"objections": ["algo"]}',
        '{"p_exito": 1.7, "p_objeciones": {}}',  # oráculo sobreconfiado fuera de rango
        '{"decision": "emitir", "confidence": 1.5, "rationale": "", "review_triggers": []}',
    ])
    verdict = _panel(provider).deliberate("ctx", _candidate())
    assert verdict.confidence == 1.0
    assert verdict.p_success == 1.0


def test_risk_profile_defaults():
    r = RiskProfile()
    assert 0.0 <= r.risk_aversion <= 1.0
    assert r.max_ruin_probability < 0.05


# ── Simulación ────────────────────────────────────────────────────────────


def _toy_outcome_model(probs_by_action: dict[str, dict[str, float]]):
    def model(state, action):
        return Distribution(outcomes=probs_by_action[action.id])

    return model


def test_monte_carlo_converge_a_las_probabilidades():
    a = Action(id="A", description="arriesgada")
    model = _toy_outcome_model({"A": {"win": 0.7, "lose": 0.3}})
    utility = lambda state, path: 1.0 if path[-1] == "win" else -1.0
    engine = MonteCarloEngine(model, utility, n_samples=4000, seed=42)

    trajs = engine.expand(WorldModel().current(), [a], depth=1)
    by_utility = {t.utility: t.probability for t in trajs}
    assert by_utility[1.0] == pytest.approx(0.7, abs=0.03)
    assert by_utility[-1.0] == pytest.approx(0.3, abs=0.03)
    assert sum(t.probability for t in trajs) == pytest.approx(1.0)
    # Reproducibilidad con la misma semilla.
    trajs2 = MonteCarloEngine(model, utility, n_samples=4000, seed=42).expand(
        WorldModel().current(), [a], depth=1
    )
    assert [t.probability for t in trajs2] == [t.probability for t in trajs]


def test_tree_search_expectimax():
    a = Action(id="A", description="a")
    b = Action(id="B", description="b")
    model = _toy_outcome_model({"A": {"w": 0.6, "l": 0.4}, "B": {"w": 0.9, "l": 0.1}})
    utility = lambda state, path: float(sum(1 for x in path if x == "w"))
    engine = TreeSearchEngine(model, utility)

    trajs = engine.expand(WorldModel().current(), [a, b], depth=2)
    # Continuación óptima: tras cualquier primer outcome conviene jugar B (0.9).
    for t in trajs:
        first_is_win = t.utility > 1.0
        expected = 0.9 + (1.0 if first_is_win else 0.0)
        assert t.utility == pytest.approx(expected)
    eu = {aid: sum(t.probability * t.utility for t in trajs if t.actions_taken[0].id == aid)
          for aid in ("A", "B")}
    assert eu["B"] == pytest.approx(1.8)
    assert eu["A"] == pytest.approx(1.5)


def test_softmax_adversary_prefiere_su_mejor_respuesta():
    atk = Action(id="atk", description="atacar")
    dfn = Action(id="dfn", description="defender")
    adversary = SoftmaxAdversary(
        candidate_actions=[atk, dfn],
        their_utility=lambda state, ours, theirs: 2.0 if theirs.id == "atk" else 0.5,
        temperature=0.5,
    )
    responses = dict(
        (a.id, p) for a, p in adversary.respond(WorldModel().current(), Action(id="x", description=""))
    )
    assert sum(responses.values()) == pytest.approx(1.0)
    assert responses["atk"] > responses["dfn"]


# ── Planificador ──────────────────────────────────────────────────────────


def _trajs(action_id: str, pairs: list[tuple[float, float]]) -> list[Trajectory]:
    action = Action(id=action_id, description=action_id)
    state = WorldModel().current()
    return [Trajectory(states=[state], probability=p, utility=u, actions_taken=[action])
            for p, u in pairs]


def test_planner_la_aversion_al_riesgo_cambia_el_ranking():
    # A: EU=0.4 pero cola en -1. B: seguro 0.1.
    trajectories = _trajs("A", [(0.7, 1.0), (0.3, -1.0)]) + _trajs("B", [(1.0, 0.1)])
    planner = ExpectedUtilityPlanner()

    neutral = planner.decide(trajectories, RiskProfile(risk_aversion=0.0))
    assert neutral[0].action.id == "A"  # sin aversión gana la de mayor EU

    averse = planner.decide(trajectories, RiskProfile(risk_aversion=0.3, cvar_alpha=0.05))
    assert averse[0].action.id == "B"  # con aversión, la segura adelanta
    # A: 0.7*0.4 + 0.3*CVaR(=-1.0) = 0.28 - 0.30 = -0.02
    a_rec = next(r for r in averse if r.action.id == "A")
    assert a_rec.risk_adjusted_utility == pytest.approx(-0.02)


def test_planner_descarta_por_ruina():
    trajectories = _trajs("A", [(0.7, 1.0), (0.3, -1.0)]) + _trajs("B", [(1.0, 0.1)])
    risk = RiskProfile(ruin_threshold=-0.5, max_ruin_probability=0.01)
    ranking = ExpectedUtilityPlanner().decide(trajectories, risk)

    assert [r.action.id for r in ranking] == ["B"]
    assert ranking[0].rejected_alternatives[0][0].id == "A"
    assert "ruina" in ranking[0].rejected_alternatives[0][1]

    # Si TODAS caen por ruina, negarse a recomendar es la salida correcta.
    all_ruin = _trajs("A", [(0.5, -1.0), (0.5, 1.0)])
    assert ExpectedUtilityPlanner().decide(all_ruin, risk) == []


def test_planner_constraints_se_vuelven_supuestos():
    ranking = ExpectedUtilityPlanner().decide(
        _trajs("A", [(1.0, 0.5)]), RiskProfile(), constraints=["presupuesto <= 50k"]
    )
    assert any("presupuesto" in a for a in ranking[0].assumptions)


# ── Ciclo OODA completo ───────────────────────────────────────────────────


class FakeForecaster(Forecaster):
    def forecast(self, state, question, horizon):
        return _forecast("fx-1", {"bien": 0.7, "mal": 0.3}, source="fake")


def _agent(panel_provider) -> "OraqloAgent":
    from oraqlo.agent import OraqloAgent
    from oraqlo.connectors import StaticConnector

    model = _toy_outcome_model({"A": {"win": 0.8, "lose": 0.2}, "B": {"win": 0.5, "lose": 0.5}})
    utility = lambda state, path: 1.0 if path[-1] == "win" else 0.0
    return OraqloAgent(
        connectors=[StaticConnector("demo", [
            Observation(entity_id="mercado", variable="tendencia", value="alcista", source="demo")
        ])],
        world_model=WorldModel(),
        forecaster=FakeForecaster(),
        simulator=TreeSearchEngine(model, utility),
        planner=ExpectedUtilityPlanner(),
        panel=_panel(panel_provider),
        ledger=CalibrationLedger(),
    )


def test_ciclo_ooda_completo_emite():
    provider = FakeProvider([
        "Plan sólido.",
        '{"objections": ["riesgo de ejecución"]}',
        '{"p_exito": 0.7, "p_objeciones": {"riesgo de ejecución": 0.2}}',
        '{"decision": "emitir", "confidence": 0.7, "rationale": "ok", "review_triggers": []}',
    ])
    agent = _agent(provider)
    report = agent.cycle("¿expandimos?", [Action(id="A", description="expandir"),
                                          Action(id="B", description="esperar")],
                         horizon=timedelta(days=90), depth=1)

    assert report.verdict.decision == "emitir"
    assert report.recommendation.action.id == "A"  # mayor EU con el modelo toy
    # La observación entró al world model y la predicción quedó registrada.
    assert agent.world_model.current().version == 1
    assert len(agent.ledger.open_forecasts) == 1
    # El contexto del panel incluyó la previsión del oráculo.
    assert "70%" in provider.calls[0][-1].content


def test_ciclo_ooda_revisar_redelibera_con_objeciones():
    reviewing = [
        "Plan v1.",
        '{"objections": ["falta validar el mercado"]}',
        '{"p_exito": 0.5, "p_objeciones": {"falta validar el mercado": 0.5}}',
        '{"decision": "revisar", "confidence": 0.4, "rationale": "dudas", "review_triggers": []}',
    ]
    emitting = [
        "Plan v2 con validación.",
        '{"objections": ["coste del retraso"]}',
        '{"p_exito": 0.65, "p_objeciones": {"coste del retraso": 0.3}}',
        '{"decision": "emitir", "confidence": 0.6, "rationale": "mejor", "review_triggers": ["churn > 5%"]}',
    ]
    provider = FakeProvider(reviewing + emitting)
    report = _agent(provider).cycle("¿expandimos?", [Action(id="A", description="expandir")],
                                    horizon=timedelta(days=90), depth=1)

    assert report.verdict.decision == "emitir"
    assert report.revisions == 1
    # La segunda deliberación vio las objeciones de la primera.
    assert "falta validar el mercado" in provider.calls[4][-1].content


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
