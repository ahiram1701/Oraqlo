"""Ensamble, forecaster cuantitativo, adversario integrado, conector de archivos y eventos del ciclo."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from oraqlo.forecaster import EnsembleForecaster, QuantForecaster, resolve_group
from oraqlo.forecaster.base import Distribution, Forecast, Forecaster
from oraqlo.memory.calibration import CalibrationLedger
from oraqlo.simulator import Action, SoftmaxAdversary, TreeSearchEngine, with_adversary
from oraqlo.world_model import Observation, WorldModel


class Fixed(Forecaster):
    def __init__(self, source, outcomes):
        self.source, self.outcomes = source, outcomes

    def forecast(self, state, question, horizon):
        return Forecast(
            id="tmp",
            question=question,
            distribution=Distribution(outcomes=dict(self.outcomes)),
            horizon=horizon,
            source=self.source,
            assumptions=[f"supuesto de {self.source}"],
        )


def _history(ledger, source, brier_target_label, n=4):
    """Siembra historial: `source` acierta (label correcto) o falla según el caso."""
    for i in range(n):
        f = Fixed(source, {"sí": 0.9, "no": 0.1}).forecast(None, "q", timedelta(days=1))
        f.id = f"{source}-{i}"
        ledger.record(f)
        ledger.resolve(f.id, brier_target_label)


def test_ensamble_pondera_por_fiabilidad_y_aprende():
    ledger = CalibrationLedger()
    _history(ledger, "bueno", "sí")  # Brier 0.02
    _history(ledger, "malo", "no")  # Brier 1.62
    ens = EnsembleForecaster(
        [Fixed("bueno", {"Sí": 0.8, "No": 0.2}), Fixed("malo", {"sí": 0.2, "no": 0.8})], ledger
    )
    f = ens.forecast(None, "¿pasa?", timedelta(days=7))
    # Etiquetas unificadas sin importar mayúsculas; domina el miembro fiable.
    assert set(f.distribution.outcomes) == {"Sí", "No"}
    assert f.distribution.outcomes["Sí"] > 0.75
    assert sum(f.distribution.outcomes.values()) == pytest.approx(1.0)
    # Los miembros quedan registrados y se cierran con el grupo.
    assert {x.id for x in ledger.open_forecasts} == {f"{f.id}::bueno", f"{f.id}::malo"}
    ledger.record(f)
    assert resolve_group(ledger, f.id, "sí") == 3
    assert ledger.open_forecasts == []


def test_ensamble_sin_historial_promedia():
    ens = EnsembleForecaster(
        [Fixed("a", {"x": 1.0}), Fixed("b", {"y": 1.0})], CalibrationLedger(), record_members=False
    )
    out = ens.forecast(None, "q", timedelta(days=1)).distribution.outcomes
    assert out == pytest.approx({"x": 0.5, "y": 0.5})


def _series(values, start=datetime(2026, 1, 1, tzinfo=UTC), step=timedelta(days=1)):
    wm = WorldModel()
    for i, v in enumerate(values):
        wm.apply(Observation("tienda", "ventas", v, "test", start + i * step))
    return wm


def test_quant_extrapola_tendencia_con_intervalo_que_crece():
    wm = _series([10, 12, 14, 16, 18, 20, 22.5, 24, 26])
    qf = QuantForecaster(wm, "tienda", "ventas")
    near = qf.forecast(wm.current(), "ventas", timedelta(days=1)).distribution
    far = qf.forecast(wm.current(), "ventas", timedelta(days=10)).distribution
    assert near.point == pytest.approx(28, abs=1.0)
    assert far.point == pytest.approx(46, abs=3.0)
    assert (far.interval[1] - far.interval[0]) > (near.interval[1] - near.interval[0])
    assert near.interval[0] < near.point < near.interval[1]


def test_quant_umbral_da_binaria_resoluble():
    wm = _series([10, 12, 14, 16, 18, 20, 22, 24, 26])
    qf = QuantForecaster(wm, "tienda", "ventas", threshold=30)
    f = qf.forecast(wm.current(), "¿ventas > 30?", timedelta(days=5))
    assert f.distribution.outcomes["> 30"] > 0.9  # tendencia clara: ~36 esperado
    ledger = CalibrationLedger()
    ledger.record(f)
    assert ledger.resolve(f.id, "> 30").brier < 0.05


def test_quant_pocas_observaciones():
    wm = _series([1, 2])
    with pytest.raises(ValueError, match="al menos 3"):
        QuantForecaster(wm, "tienda", "ventas").forecast(wm.current(), "q", timedelta(days=1))


def test_adversario_cambia_la_mejor_jugada():
    """Sin adversario 'agresiva' gana; con un rival que castiga la agresividad, no."""
    ours = [Action("agresiva", "precio bajo"), Action("prudente", "precio estable")]
    theirs = [Action("guerra", "guerra de precios"), Action("paz", "mantener")]

    def outcome(state, our, their):
        if our.id == "agresiva":
            return Distribution(
                outcomes={"gana": 0.1, "pierde": 0.9}
                if their.id == "guerra"
                else {"gana": 0.9, "pierde": 0.1}
            )
        return Distribution(outcomes={"gana": 0.6, "pierde": 0.4})

    # Al rival le conviene la guerra justo cuando somos agresivos.
    their_utility = lambda state, our, their: (
        (1.0 if their.id == "guerra" else 0.0) * (our.id == "agresiva")
    )
    utility = lambda state, path: 1.0 if path[-1] == "gana" else 0.0

    def best(model):
        trajs = TreeSearchEngine(model, utility).expand(None, ours, depth=1)
        ev = {
            a.id: sum(t.probability * t.utility for t in trajs if t.actions_taken[0].id == a.id)
            for a in ours
        }
        return max(ev, key=ev.get), ev

    passive = with_adversary(
        SoftmaxAdversary(theirs, lambda s, o, t: 0.0, temperature=1.0), outcome
    )
    smart = with_adversary(SoftmaxAdversary(theirs, their_utility, temperature=0.1), outcome)
    assert best(passive)[0] == "prudente" or best(passive)[1]["agresiva"] == pytest.approx(0.5)
    choice, ev = best(smart)
    assert choice == "prudente"
    assert ev["agresiva"] < 0.2


def test_file_connector_solo_entrega_lo_nuevo(tmp_path):
    from oraqlo.connectors import FileConnector

    path = tmp_path / "obs.csv"
    path.write_text(
        "entity_id,variable,value,timestamp\nt,ventas,10,2026-01-01T00:00:00\n", "utf-8"
    )
    conn = FileConnector(path)
    first = list(conn.poll())
    assert len(first) == 1 and first[0].value == 10 and first[0].timestamp.tzinfo is not None
    with path.open("a", encoding="utf-8") as fh:
        fh.write("t,ventas,12.5,2026-01-02T00:00:00\n")
    second = list(conn.poll())
    assert [o.value for o in second] == [12.5]
    assert list(conn.poll()) == []


def test_ciclo_emite_eventos_en_orden():
    from test_smoke import FakeProvider, _agent

    provider = FakeProvider(
        [
            "Plan.",
            '{"objections": ["o1"]}',
            '{"p_exito": 0.7, "p_objeciones": {"o1": 0.2}}',
            '{"decision": "emitir", "confidence": 0.7, "rationale": "ok", "review_triggers": []}',
        ]
    )
    events = []
    _agent(provider).cycle(
        "¿expandimos?",
        [Action("A", "expandir"), Action("B", "esperar")],
        horizon=timedelta(days=90),
        depth=1,
        on_event=lambda kind, data: events.append((kind, data)),
    )
    phases = [d["name"] for k, d in events if k == "phase"]
    assert phases == ["observar", "prever", "simular", "planificar", "deliberar"]
    assert [d["role"] for k, d in events if k == "turn"] == [
        "estratega",
        "red_team",
        "oraculo",
        "juez",
    ]
    assert events[-1][0] == "verdict" and events[-1][1]["decision"] == "emitir"
