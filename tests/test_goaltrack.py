"""Tests del seguimiento de objetivos: store, tracker, cierre y TUI."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from oraqlo.forecaster.base import Distribution, Forecast
from oraqlo.goals import (
    OUTCOME_ACHIEVED,
    OUTCOME_NOT_ACHIEVED,
    GoalPlan,
    Milestone,
    PlanStep,
)
from oraqlo.goaltrack import GoalStore, GoalTracker, GoalTrackError, TrackedMilestone
from oraqlo.memory.calibration import CalibrationLedger

from test_smoke import FakeProvider  # doble compartido


def _binary(fid: str, p: float, label: str) -> Forecast:
    return Forecast(
        id=fid, question=f"[{label}] maratón",
        distribution=Distribution(outcomes={OUTCOME_ACHIEVED: p, OUTCOME_NOT_ACHIEVED: 1 - p}),
        horizon=timedelta(days=180),
    )


def _plan() -> GoalPlan:
    return GoalPlan(
        goal="maratón sub-4h", horizon=timedelta(days=180),
        baseline=_binary("b1", 0.2, "statu quo"), with_plan=_binary("w1", 0.6, "con plan"),
        levers=[], external_conditions=[],
        summary="Plan progresivo",
        steps=[PlanStep(order=1, action="base aeróbica", when="mes 1-2", why="resistencia")],
        milestones=[
            Milestone(text="media en 1h55", target_date="2026-10-01", signal="ritmo"),
            Milestone(text="tirada de 30km", target_date="2026-12-01", signal="distancia"),
        ],
    )


def _tracker(tmp_path, provider=None) -> tuple[GoalTracker, GoalStore, CalibrationLedger]:
    store = GoalStore(tmp_path / "goals.json")
    ledger = CalibrationLedger()
    return GoalTracker(store, ledger, provider, "fake" if provider else ""), store, ledger


def test_track_y_roundtrip_persistente(tmp_path):
    tracker, store, _ = _tracker(tmp_path)
    tracked = tracker.track(_plan())
    assert tracked.initial_p == pytest.approx(0.6)
    assert tracked.baseline_p == pytest.approx(0.2)
    assert tracked.current_p == pytest.approx(0.6)  # sin check-ins aún
    assert len(tracked.milestones) == 2
    assert tracked.deadline == (date.today() + timedelta(days=180)).isoformat()

    # Otra instancia del store ve el objetivo completo (roundtrip JSON).
    reloaded = GoalStore(store.path).get(tracked.id)
    assert reloaded.goal == "maratón sub-4h"
    assert reloaded.milestones[0].text == "media en 1h55"
    assert reloaded.steps[0].action == "base aeróbica"


def test_checkin_actualiza_trayectoria(tmp_path):
    provider = FakeProvider([
        '{"p_logro": 0.7, "comment": "buen ritmo", "replan_recommended": false}',
    ])
    tracker, store, _ = _tracker(tmp_path, provider)
    tracked = tracker.track(_plan())

    checkin = tracker.check_in(tracked.id, "semana 1: 3 entrenamientos, 28 km")
    assert checkin.p_estimate == pytest.approx(0.7)
    assert checkin.replan_recommended is False
    g = store.get(tracked.id)
    assert g.current_p == pytest.approx(0.7)
    assert g.trajectory == [pytest.approx(0.6), pytest.approx(0.7)]
    # El prompt de re-evaluación incluyó plan, hitos y la nota.
    prompt = provider.calls[0][-1].content
    assert "media en 1h55" in prompt and "28 km" in prompt

    # Persistió: recargar el store conserva el check-in.
    assert GoalStore(store.path).get(tracked.id).checkins[0].note.startswith("semana 1")


def test_checkin_hito_fallado_fuerza_replan_recomendado(tmp_path):
    provider = FakeProvider([
        '{"p_logro": 0.5, "comment": "regular", "replan_recommended": false}',
    ])
    tracker, store, _ = _tracker(tmp_path, provider)
    tracked = tracker.track(_plan())
    tracker.set_milestone(tracked.id, 0, "fallado")

    checkin = tracker.check_in(tracked.id, "no llegué al ritmo")
    # Aunque el LLM no lo recomendó, un hito fallado lo fuerza.
    assert checkin.replan_recommended is True


def test_replan_conserva_cumplidos_y_versiona(tmp_path):
    provider = FakeProvider([
        '{"summary": "Plan v2 ajustado", "steps": [{"order": 1, "action": "más suave",'
        ' "when": "ya", "why": "evitar lesión"}], "milestones":'
        ' [{"text": "nuevo hito", "target_date": "2026-11-15", "signal": "nueva señal"}]}',
    ])
    tracker, store, _ = _tracker(tmp_path, provider)
    tracked = tracker.track(_plan())
    tracker.set_milestone(tracked.id, 0, "cumplido")
    tracker.set_milestone(tracked.id, 1, "fallado")

    g = tracker.replan(tracked.id)
    assert g.plan_version == 2
    assert g.summary == "Plan v2 ajustado"
    # El cumplido se conserva; el fallado desaparece; entra el nuevo.
    assert [(m.text, m.status) for m in g.milestones] == [
        ("media en 1h55", "cumplido"), ("nuevo hito", "pendiente"),
    ]
    assert g.steps[0].action == "más suave"


def test_close_resuelve_predicciones_y_tolera_resueltas(tmp_path):
    tracker, store, ledger = _tracker(tmp_path)
    ledger.record(_binary("b1", 0.2, "statu quo"))
    ledger.record(_binary("w1", 0.6, "con plan"))
    tracked = tracker.track(_plan())

    warnings = tracker.close(tracked.id, achieved=True)
    assert store.get(tracked.id).status == "logrado"
    assert ledger.open_forecasts == []
    # Brier del "con plan" (dijo 60% se logra, se logró): (0.6-1)² + 0.4² = 0.32
    briers = {r.forecast.question: r.brier for r in ledger.resolved_forecasts}
    assert briers["[con plan] maratón"] == pytest.approx(0.32)
    assert briers["[statu quo] maratón"] == pytest.approx(1.28)
    assert len(warnings) == 2

    # Cerrar otro objetivo cuyas predicciones ya no están abiertas: avisa, no falla.
    tracked2 = tracker.track(_plan())
    warnings2 = tracker.close(tracked2.id, achieved=False)
    assert any("ya no estaba abierta" in w for w in warnings2)
    # Un objetivo cerrado no admite check-ins.
    with pytest.raises(GoalTrackError, match="logrado"):
        GoalTracker(store, ledger, FakeProvider(["{}"]), "fake").check_in(tracked.id, "nota")


def test_delete_descarta_abiertas_y_conserva_resueltas(tmp_path):
    tracker, store, ledger = _tracker(tmp_path)
    ledger.record(_binary("b1", 0.2, "statu quo"))
    ledger.record(_binary("w1", 0.6, "con plan"))
    tracked = tracker.track(_plan())

    warnings = tracker.delete(tracked.id)
    assert store.list_all() == []                # el objetivo desaparece
    assert ledger.open_forecasts == []           # sus predicciones se descartaron
    assert ledger.resolved_forecasts == []       # descartar NO puntúa (sin Brier)
    assert len(warnings) == 2
    with pytest.raises(KeyError):
        store.get(tracked.id)
    # Persistió: recargar el store no lo trae de vuelta.
    assert GoalStore(store.path).list_all() == []


def test_delete_no_toca_predicciones_ya_resueltas(tmp_path):
    tracker, store, ledger = _tracker(tmp_path)
    ledger.record(_binary("b1", 0.2, "statu quo"))
    ledger.record(_binary("w1", 0.6, "con plan"))
    tracked = tracker.track(_plan())
    tracker.close(tracked.id, achieved=True)     # resuelve ambas (historial legítimo)
    assert len(ledger.resolved_forecasts) == 2

    tracker.delete(tracked.id)
    assert store.list_all() == []
    assert len(ledger.resolved_forecasts) == 2   # calibración intacta


def test_overdue_milestones(tmp_path):
    tracker, store, _ = _tracker(tmp_path)
    tracked = tracker.track(_plan())
    g = store.get(tracked.id)
    g.milestones.append(TrackedMilestone(text="vencido", target_date="2020-01-01", signal="s"))
    store.update(g)

    overdue = tracker.overdue_milestones()
    assert len(overdue) == 1
    assert overdue[0][1].text == "vencido"
    # Marcarlo cumplido lo saca de vencidos.
    tracker.set_milestone(tracked.id, len(g.milestones) - 1, "cumplido")
    assert tracker.overdue_milestones() == []


def test_tui_pestana_objetivos(tmp_path):
    pytest.importorskip("textual")
    import asyncio

    from textual.widgets import DataTable, TabbedContent

    from oraqlo.tui.app import OraqloTUI

    # Sembrar un objetivo trackeado con las predicciones abiertas en el ledger.
    from oraqlo.memory.store import JsonLedger

    ledger = JsonLedger(tmp_path / "ledger.json")
    ledger.record(_binary("b1", 0.2, "statu quo"))
    ledger.record(_binary("w1", 0.6, "con plan"))
    store = GoalStore(tmp_path / "goals.json")
    GoalTracker(store, ledger).track(_plan())

    async def go():
        app = OraqloTUI(case_path=tmp_path / "caso.json", ledger_path=tmp_path / "ledger.json",
                        goals_path=tmp_path / "goals.json", settings_path=tmp_path / "s.json")
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            app.query_one(TabbedContent).active = "tab-goals"
            await pilot.pause()
            goals_table = app.query_one("#goals-table", DataTable)
            assert goals_table.row_count == 1
            assert app.query_one("#milestones-table", DataTable).row_count == 2

            # Marcar el hito seleccionado como cumplido actualiza el estado.
            await pilot.click("#btn-milestone-done")
            await pilot.pause()
            assert app.goal_store.list_all()[0].milestones[0].status == "cumplido"

            # Cerrar como logrado (con confirmación) resuelve las predicciones.
            await pilot.click("#btn-goal-won")
            await pilot.pause()
            await pilot.click("#confirm-yes")
            await pilot.pause()
            assert app.goal_store.list_all()[0].status == "logrado"
            assert app.ledger.open_forecasts == []
            assert len(app.ledger.resolved_forecasts) == 2

    asyncio.run(go())


def test_tui_eliminar_objetivo_con_confirmacion(tmp_path):
    pytest.importorskip("textual")
    import asyncio

    from textual.widgets import DataTable, TabbedContent

    from oraqlo.memory.store import JsonLedger
    from oraqlo.tui.app import ConfirmScreen, OraqloTUI

    ledger = JsonLedger(tmp_path / "ledger.json")
    ledger.record(_binary("b1", 0.2, "statu quo"))
    ledger.record(_binary("w1", 0.6, "con plan"))
    store = GoalStore(tmp_path / "goals.json")
    GoalTracker(store, ledger).track(_plan())

    async def go():
        app = OraqloTUI(case_path=tmp_path / "caso.json", ledger_path=tmp_path / "ledger.json",
                        goals_path=tmp_path / "goals.json", settings_path=tmp_path / "s.json")
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            app.query_one(TabbedContent).active = "tab-goals"
            await pilot.pause()
            assert app.query_one("#goals-table", DataTable).row_count == 1

            # Cancelar NO elimina.
            await pilot.click("#btn-goal-delete")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.click("#confirm-no")
            await pilot.pause()
            assert len(app.goal_store.list_all()) == 1

            # Confirmar elimina el objetivo y descarta sus predicciones abiertas.
            await pilot.click("#btn-goal-delete")
            await pilot.pause()
            await pilot.click("#confirm-yes")
            await pilot.pause()
            assert app.goal_store.list_all() == []
            assert app.ledger.open_forecasts == []
            assert app.ledger.resolved_forecasts == []  # descartadas, no puntuadas
            assert app.query_one("#goals-table", DataTable).row_count == 0

    asyncio.run(go())
