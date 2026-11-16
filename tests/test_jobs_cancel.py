"""Cancelar un trabajo largo: en cola, a mitad del flujo, y sin tragárselo un except."""

from __future__ import annotations

import contextlib
import threading

from oraqlo.config import load_config
from oraqlo.services.core import OraqloService
from oraqlo.services.jobs import CANCELLED_MSG, INTERRUPTED_MSG, JobRunner
from oraqlo.store import Store
from test_api import env  # noqa: F401  (fixture compartida)


def _runner(tmp_path, workers: int = 1) -> JobRunner:
    service = OraqloService(
        load_config(tmp_path / "none.toml", home=tmp_path), Store(tmp_path / "o.db")
    )
    return JobRunner(service, workers=workers)


def _wait(runner: JobRunner, job_id: str, status: str) -> dict:
    for _ in range(200):
        job = runner.get(job_id)
        if job and job["status"] == status:
            return job
        threading.Event().wait(0.02)
    raise AssertionError(f"{job_id} no llegó a {status}: {runner.get(job_id)}")


def test_cancelar_a_mitad_detiene_el_flujo_aunque_capture_excepciones(tmp_path):
    runner = _runner(tmp_path)
    started, release = threading.Event(), threading.Event()
    reached_end: list[bool] = []

    def flow(emit):
        emit("phase", {"name": "prever"})
        started.set()
        release.wait(5)
        with contextlib.suppress(Exception):  # como research(): degrada ante cualquier Exception
            emit("log", {"msg": "sigo"})
        reached_end.append(True)
        return {"ok": True}

    runner.handlers["lento"] = flow
    job_id = runner.submit("lento")
    assert started.wait(5)
    assert runner.cancel(job_id)["status"] == "running"  # se detiene en el próximo evento
    release.set()
    job = _wait(runner, job_id, "cancelled")
    assert job["error"] == CANCELLED_MSG
    assert reached_end == []
    runner.shutdown(wait=True)


def test_cancelar_en_cola_no_llega_a_correr(tmp_path):
    runner = _runner(tmp_path, workers=1)
    gate = threading.Event()
    ran: list[str] = []
    runner.handlers["bloquea"] = lambda emit: (gate.wait(5), {"ok": True})[1]
    runner.handlers["despues"] = lambda emit: (ran.append("x"), {"ok": True})[1]
    first = runner.submit("bloquea")
    queued = runner.submit("despues")
    assert runner.cancel(queued)["status"] == "cancelled"
    gate.set()
    _wait(runner, first, "done")
    runner.shutdown(wait=True)
    assert ran == [] and runner.get(queued)["status"] == "cancelled"


def test_cancelar_algo_terminado_o_inexistente(tmp_path):
    runner = _runner(tmp_path)
    runner.handlers["rapido"] = lambda emit: {"ok": True}
    job_id = runner.submit("rapido")
    _wait(runner, job_id, "done")
    assert runner.cancel(job_id)["status"] == "done"
    assert runner.cancel("no-existe") is None
    runner.shutdown(wait=True)


def test_endpoint_de_cancelar_id_desconocido(tmp_path):
    from fastapi.testclient import TestClient

    from oraqlo.api.app import create_app
    from oraqlo.services.network import TailscaleServe

    service = OraqloService(
        load_config(tmp_path / "none.toml", home=tmp_path), Store(tmp_path / "o.db")
    )
    app = create_app(service, background=False, tailscale=TailscaleServe(exe=""))
    with TestClient(app) as client:
        assert client.post("/api/jobs/nada/cancel").status_code == 404


def test_objetivo_anuncia_sus_pasos(env, monkeypatch):
    from oraqlo.goals import GoalStrategist

    _, service, *_ = env

    def fake_chat(self, system, user, temperature=0.2):
        if "p_logro" in system:
            return {"p_logro": 0.4, "assumptions": []}
        if "levers" in system:
            return {"levers": [{"text": "entrenar"}], "external_conditions": []}
        return {"summary": "plan", "steps": [{"order": 1, "action": "correr"}], "milestones": []}

    monkeypatch.setattr(GoalStrategist, "_chat", fake_chat)
    phases: list[str] = []
    service.achieve_goal(
        "Correr un maratón",
        90,
        research=False,
        emit=lambda t, d: phases.append(d["name"]) if t == "phase" else None,
    )
    assert phases == ["linea_base", "palancas", "plan", "deliberar", "con_plan"]


def test_reintentar_lo_interrumpido_por_un_reinicio(tmp_path, env):
    runner = _runner(tmp_path)
    seen: list[int] = []
    runner.handlers["dato"] = lambda x, emit: (seen.append(x), {"ok": True})[1]
    with runner.db.tx() as conn:  # como si el servidor se hubiera parado a mitad
        conn.execute(
            "INSERT INTO jobs (id, kind, status, params) VALUES ('j1', 'dato', 'running', ?)",
            ('{"x": 7}',),
        )
    assert runner.recover() == 1
    assert runner.get("j1")["error"] == INTERRUPTED_MSG

    new_id = runner.retry("j1")
    assert new_id and new_id != "j1"
    _wait(runner, new_id, "done")
    assert seen == [7]  # mismos parámetros
    assert runner.retry(new_id) is None  # lo terminado no se reintenta
    assert runner.retry("no-existe") is None
    runner.shutdown(wait=True)

    client, *_ = env
    assert client.post("/api/jobs/no-existe/retry").status_code == 404
