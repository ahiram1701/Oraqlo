"""Tests del archivo de caso, el ledger persistente y el montaje de la TUI."""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from oraqlo.casefile import CaseError, load_case, parse_case, template_json
from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.memory.store import JsonLedger

CASES_DIR = Path(__file__).resolve().parent.parent / "cases"


def test_template_es_un_caso_valido():
    spec = parse_case(json.loads(template_json()))
    assert len(spec.actions) == 2
    assert spec.horizon == timedelta(days=180)
    # El modelo y la utilidad enchufan con el simulador.
    dist = spec.outcome_model()(None, spec.actions[0])
    dist.validate()
    assert spec.utility_fn()(None, ("sale bien",)) == 1.0


def test_caso_ejemplo_cafeterias_carga():
    spec = load_case(CASES_DIR / "cafeterias.json")
    assert len(spec.actions) == 3
    assert spec.risk.ruin_threshold == -0.9


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda d: d.pop("question"), "question"),
        (lambda d: d.update(horizon_days=-1), "horizon_days"),
        (lambda d: d["actions"]["opcion-a"]["outcomes"].update({"sale bien": 0.9}), "suman"),
        (lambda d: d["utilities"].pop("sale mal"), "sin utilidad"),
    ],
)
def test_casos_invalidos_dan_errores_accionables(mutation, expected):
    data = json.loads(template_json())
    mutation(data)
    with pytest.raises(CaseError, match=expected):
        parse_case(data)


def test_json_ledger_persiste_y_recarga(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = JsonLedger(path)
    ledger.record(
        Forecast(
            id="f1",
            question="¿sube?",
            distribution=Distribution(outcomes={"sí": 0.6, "no": 0.4}),
            horizon=timedelta(days=30),
            assumptions=["mercado estable"],
            source="test",
        )
    )
    # Otra instancia sobre el mismo archivo ve la predicción abierta...
    reloaded = JsonLedger(path)
    assert [f.id for f in reloaded.open_forecasts] == ["f1"]
    assert reloaded.open_forecasts[0].assumptions == ["mercado estable"]

    # ...y la resolución también sobrevive, con su Brier.
    resolved = reloaded.resolve("f1", "sí")
    third = JsonLedger(path)
    assert third.open_forecasts == []
    assert third.reliability("test") == pytest.approx(resolved.brier)

    # Eliminar una resuelta también persiste (y la fiabilidad se recalcula).
    third.delete_resolved("f1")
    fourth = JsonLedger(path)
    assert fourth.resolved_forecasts == []
    assert fourth.reliability("test") is None


def test_json_ledger_discard_persiste(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = JsonLedger(path)
    ledger.record(
        Forecast(id="fd", question="¿?", horizon=timedelta(days=5),
                 distribution=Distribution(outcomes={"a": 1.0}), source="test")
    )
    ledger.discard("fd")
    assert JsonLedger(path).open_forecasts == []


def test_case_to_dict_ida_y_vuelta():
    from oraqlo.casefile import case_to_dict

    original = json.loads(template_json())
    spec = parse_case(original)
    roundtrip = case_to_dict(spec)
    # Re-parsear el dict serializado produce un caso equivalente.
    spec2 = parse_case(roundtrip)
    assert spec2.question == spec.question
    assert spec2.horizon == spec.horizon
    assert spec2.outcomes == spec.outcomes
    assert spec2.utilities == spec.utilities
    assert spec2.risk == spec.risk


def test_render_report_markdown():
    from oraqlo.panel import Verdict
    from oraqlo.report import render_report_markdown, slugify
    from oraqlo.simulator.base import Action
    from oraqlo.strategy.planner import Recommendation

    forecast = Forecast(
        id="abc",
        question="¿expandir?",
        distribution=Distribution(outcomes={"crece": 0.6, "estanca": 0.4}),
        horizon=timedelta(days=90),
        assumptions=["mercado estable"],
    )
    ranking = [Recommendation(
        action=Action(id="a", description="expandir"),
        expected_utility=0.5, risk_adjusted_utility=0.4, confidence=0.6,
    )]
    verdict = Verdict(
        decision="emitir", confidence=0.7, rationale="razonable",
        objections=["riesgo X"], review_triggers=["churn > 5%"],
        transcript=[("estratega", "plan"), ("juez", '{"decision": "emitir"}')],
    )
    md = render_report_markdown("¿expandir?", forecast, ranking, verdict, "m1", "cloud")
    for fragment in ("# Informe estratégico", "## Previsión del oráculo", "crece | 60.0%",
                     "## Ranking del planificador", "EMITIR", "churn > 5%",
                     "## Transcript del debate", "mercado estable"):
        assert fragment in md, f"falta: {fragment}"
    assert "Fuentes consultadas" not in md  # sin investigación, sin sección
    md_research = render_report_markdown(
        "¿expandir?", forecast, ranking, verdict, "m1", "cloud",
        research_facts=[("El mercado creció 12%", "https://x.example")],
    )
    assert "## Fuentes consultadas" in md_research
    assert "https://x.example" in md_research
    assert slugify("¿Expandir a Chile?") == "expandir-a-chile"


def test_render_goal_report_markdown():
    from oraqlo.goals import (
        ExternalCondition, GoalPlan, Lever, Milestone, OUTCOME_ACHIEVED,
        OUTCOME_NOT_ACHIEVED, PlanStep,
    )
    from oraqlo.panel import Verdict
    from oraqlo.report import render_goal_report_markdown

    def _binary(fid, p, label):
        return Forecast(
            id=fid, question=f"[{label}] maratón",
            distribution=Distribution(outcomes={OUTCOME_ACHIEVED: p, OUTCOME_NOT_ACHIEVED: 1 - p}),
            horizon=timedelta(days=180),
        )

    plan = GoalPlan(
        goal="maratón sub-4h", horizon=timedelta(days=180),
        baseline=_binary("b1", 0.2, "statu quo"), with_plan=_binary("w1", 0.6, "con plan"),
        levers=[Lever(text="entrenar 4 días", impact="alto", effort="medio")],
        external_conditions=[ExternalCondition(text="no lesionarte", probability=0.8)],
        summary="Plan progresivo",
        steps=[PlanStep(order=1, action="base aeróbica", when="mes 1-2", why="resistencia")],
        milestones=[Milestone(text="media en 1h55", target_date="2026-10-01", signal="ritmo")],
        verdict=Verdict(decision="emitir", confidence=0.6, rationale="razonable",
                        objections=["constancia"], review_triggers=["dolor persistente"]),
    )
    md = render_goal_report_markdown(plan, "m1", "cloud",
                                     research_facts=[("hecho", "https://x.example")])
    for fragment in ("# Plan de objetivo", "## Probabilidades", "20%", "60%", "**+40%**",
                     "## Qué puedes hacer", "entrenar 4 días",
                     "## Qué tiene que pasar", "no lesionarte",
                     "## Qué tienes que hacer", "base aeróbica",
                     "## Hitos", "2026-10-01", "EMITIR", "dolor persistente",
                     "## Fuentes consultadas"):
        assert fragment in md, f"falta: {fragment}"

    # Uplift negativo: advertencia explícita.
    plan.with_plan = _binary("w2", 0.1, "con plan")
    md_neg = render_goal_report_markdown(plan, "m1", "cloud")
    assert "NO aumenta la probabilidad" in md_neg


def _seed_ledger(path: Path) -> None:
    from datetime import datetime, timezone

    ledger = JsonLedger(path)
    ledger.record(
        Forecast(
            id="f-tui",
            question="¿la TUI muestra esto?",
            distribution=Distribution(outcomes={"sí": 0.7, "no": 0.3}),
            horizon=timedelta(days=10),
            assumptions=["supuesto visible"],
            source="test",
            created_at=datetime(2026, 7, 10, 15, 30, tzinfo=timezone.utc),
        )
    )


def test_tui_monta_formulario_y_modo_json(tmp_path):
    pytest.importorskip("textual")
    import asyncio

    from textual.widgets import DataTable, RichLog, TabbedContent, TextArea

    from oraqlo.tui.app import OraqloTUI
    from oraqlo.tui.case_form import CaseForm

    async def go():
        app = OraqloTUI(case_path=tmp_path / "caso.json", ledger_path=tmp_path / "ledger.json",
                        settings_path=tmp_path / "settings.json")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one(TabbedContent).active = "tab-caso"
            await pilot.pause()
            form = app.query_one("#case-form", CaseForm)
            assert app.query_one("#log", RichLog) is not None
            assert app.query_one("#open-table", DataTable) is not None
            # El formulario arranca con la plantilla y produce un caso válido.
            parse_case(form.to_dict())
            # Validación en vivo visible.
            status = str(app.query_one("#cf-status").render())
            assert "válido" in status
            # Conmutar a modo JSON refleja los datos del formulario.
            await pilot.click("#btn-mode")
            await pilot.pause()
            editor = app.query_one("#case-editor", TextArea)
            assert editor.display
            data = json.loads(editor.text)
            parse_case(data)
            assert data["question"] == form.to_dict()["question"]
            # Y volver al formulario funciona si el JSON es válido.
            await pilot.click("#btn-mode")
            await pilot.pause()
            assert form.display and not editor.display

    asyncio.run(go())


def test_tui_ledger_ofrece_los_escenarios_para_resolver(tmp_path):
    pytest.importorskip("textual")
    import asyncio

    from textual.widgets import Select, Static

    from oraqlo.tui.app import OraqloTUI

    ledger_path = tmp_path / "ledger.json"
    _seed_ledger(ledger_path)

    async def go():
        app = OraqloTUI(case_path=tmp_path / "caso.json", ledger_path=ledger_path,
                        settings_path=tmp_path / "settings.json")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            from textual.widgets import TabbedContent

            app.query_one(TabbedContent).active = "tab-ledger"
            await pilot.pause()
            # Al montar, la primera predicción abierta ya está seleccionada:
            # su detalle es visible y el Select ofrece sus escenarios sin teclear.
            detail = str(app.query_one("#forecast-detail", Static).render())
            assert "¿la TUI muestra esto?" in detail
            # Las abiertas muestran creación y vencimiento (creación + horizonte).
            from textual.widgets import DataTable

            row = app.query_one("#open-table", DataTable).get_row_at(0)
            assert "2026-07-10 15:30" in row
            assert "2026-07-20" in row  # 10 días de horizonte
            select = app.query_one("#actual-select", Select)
            assert select.value == "sí"  # el escenario más probable, preseleccionado
            # Resolver con el escenario seleccionado calcula el Brier y limpia la tabla.
            await pilot.click("#btn-resolve")
            await pilot.pause()
            assert app.ledger.open_forecasts == []
            assert app.ledger.reliability("test") == pytest.approx((0.7 - 1) ** 2 + 0.3**2)

    asyncio.run(go())


def test_tui_eliminar_abierta_y_resuelta_con_confirmacion(tmp_path):
    pytest.importorskip("textual")
    import asyncio

    from textual.widgets import DataTable, TabbedContent

    from oraqlo.tui.app import ConfirmScreen, OraqloTUI

    ledger_path = tmp_path / "ledger.json"
    seed = JsonLedger(ledger_path)
    seed.record(Forecast(id="f-open", question="abierta", horizon=timedelta(days=5),
                         distribution=Distribution(outcomes={"a": 0.5, "b": 0.5}), source="t"))
    seed.record(Forecast(id="f-res", question="resuelta", horizon=timedelta(days=5),
                         distribution=Distribution(outcomes={"a": 0.5, "b": 0.5}), source="t"))
    seed.resolve("f-res", "a")

    async def go():
        app = OraqloTUI(case_path=tmp_path / "caso.json", ledger_path=ledger_path,
                        settings_path=tmp_path / "settings.json")
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            app.query_one(TabbedContent).active = "tab-ledger"
            await pilot.pause()
            # La tabla de resueltas muestra el historial.
            assert app.query_one("#resolved-table", DataTable).row_count == 1

            # Cancelar la confirmación NO elimina.
            await pilot.click("#btn-delete-open")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.click("#confirm-no")
            await pilot.pause()
            assert len(app.ledger.open_forecasts) == 1

            # Confirmar elimina la abierta sin tocar la calibración.
            await pilot.click("#btn-delete-open")
            await pilot.pause()
            await pilot.click("#confirm-yes")
            await pilot.pause()
            assert app.ledger.open_forecasts == []
            assert app.ledger.reliability("t") is not None

            # Eliminar la resuelta borra el historial de calibración.
            await pilot.click("#btn-delete-resolved")
            await pilot.pause()
            await pilot.click("#confirm-yes")
            await pilot.pause()
            assert app.ledger.resolved_forecasts == []
            assert app.ledger.reliability("t") is None
            # Y la eliminación persistió en disco.
            assert JsonLedger(ledger_path).resolved_forecasts == []

    asyncio.run(go())


def test_tui_pestana_preguntar(tmp_path):
    pytest.importorskip("textual")
    import asyncio

    from textual.widgets import Button, Input, RichLog, TabbedContent

    from oraqlo.tui.app import OraqloTUI

    async def go():
        app = OraqloTUI(case_path=tmp_path / "caso.json", ledger_path=tmp_path / "ledger.json",
                        settings_path=tmp_path / "settings.json")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            # Preguntar es la primera pestaña: el camino más corto es el primero.
            tabs = app.query_one(TabbedContent)
            assert tabs.active == "tab-ask"
            assert app.query_one("#ask-question", Input) is not None
            assert app.query_one("#ask-log", RichLog) is not None
            # La investigación web viene activada por defecto.
            from textual.widgets import Switch

            assert app.query_one("#ask-research", Switch).value is True
            # Con la pregunta vacía no se lanza nada (aviso, no llamada LLM).
            await pilot.click("#btn-ask-full")
            await pilot.pause()
            assert not app.query_one("#btn-ask-full", Button).disabled  # no hay worker corriendo
            # El modo objetivo existe y tampoco se lanza con objetivo vacío.
            await pilot.click("#btn-ask-goal")
            await pilot.pause()
            assert not app.query_one("#btn-ask-goal", Button).disabled
            # La pestaña Práctica existe con la autonomía APAGADA por defecto
            # (cuesta llamadas LLM: opt-in explícito).
            from textual.widgets import Switch

            assert app.query_one("#practice-auto", Switch).value is False
            assert app._practice_timer is None

    asyncio.run(go())


def test_practica_autonoma_persiste_entre_sesiones(tmp_path):
    pytest.importorskip("textual")
    import asyncio

    from textual.widgets import Input, Switch

    from oraqlo.tui.app import OraqloTUI

    paths = dict(case_path=tmp_path / "caso.json", ledger_path=tmp_path / "ledger.json",
                 goals_path=tmp_path / "goals.json", settings_path=tmp_path / "settings.json")

    async def go():
        # Sesión 1: activar la práctica autónoma (sin pasada real: parcheamos el run).
        app = OraqloTUI(**paths)
        app._handle_practice_run = lambda: None  # sin red en tests
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            app.query_one("#practice-interval", Input).value = "30"
            app.query_one("#practice-auto", Switch).value = True
            await pilot.pause()
            assert app._practice_timer is not None

        settings = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        assert settings["practice_auto"] is True
        assert settings["practice_interval_min"] == 30.0

        # Sesión 2: se restaura ENCENDIDA con el intervalo guardado, timer armado,
        # y SIN pasada inmediata (abrir la TUI no debe costar llamadas LLM).
        runs = []
        app2 = OraqloTUI(**paths)
        app2._handle_practice_run = lambda: runs.append(1)
        async with app2.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            assert app2.query_one("#practice-auto", Switch).value is True
            assert app2.query_one("#practice-interval", Input).value == "30"
            assert app2._practice_timer is not None
            assert runs == []  # reanudación silenciosa

            # Apagarla también persiste.
            app2.query_one("#practice-auto", Switch).value = False
            await pilot.pause()
            assert app2._practice_timer is None

        settings = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        assert settings["practice_auto"] is False

        # Sesión 3: arranca apagada.
        app3 = OraqloTUI(**paths)
        async with app3.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            assert app3.query_one("#practice-auto", Switch).value is False
            assert app3._practice_timer is None

    asyncio.run(go())


def test_case_form_utilidades_contradictorias(tmp_path):
    pytest.importorskip("textual")
    import asyncio

    from oraqlo.casefile import CaseError
    from oraqlo.tui.app import OraqloTUI
    from oraqlo.tui.case_form import CaseForm

    caso = {
        "question": "¿?", "horizon_days": 30,
        "actions": {
            "a": {"description": "A", "outcomes": {"gana": 1.0}},
            "b": {"description": "B", "outcomes": {"gana": 1.0}},
        },
        "utilities": {"gana": 1.0},
    }

    async def go():
        app = OraqloTUI(case_path=tmp_path / "caso.json", ledger_path=tmp_path / "ledger.json",
                        settings_path=tmp_path / "settings.json")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            form = app.query_one("#case-form", CaseForm)
            form.load_dict(caso)
            await pilot.pause()
            parse_case(form.to_dict())  # coherente: misma utilidad en ambas acciones
            # Cambiar la utilidad de "gana" solo en la acción visible → contradicción.
            assert form._outcome_rows() == [("gana", "1.0", "1.0")]
            _, _, _, utility_input = form._rows[0]
            utility_input.value = "0.2"
            await pilot.pause()
            with pytest.raises(CaseError, match="contradictorias"):
                form.to_dict()

    asyncio.run(go())
