"""Fiabilidad: tareas programadas, crecimiento de datos, migraciones y práctica."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta

import pytest

from oraqlo.services.scheduler import RETRY_AFTER, PeriodicTask, Scheduler
from oraqlo.store import Store


def test_tarea_que_falla_se_reintenta_pronto_y_queda_registrada(tmp_path):
    store = Store(tmp_path / "o.db")
    sched = Scheduler(store.settings)
    state = {"fail": True, "runs": 0}

    def backup():
        state["runs"] += 1
        if state["fail"]:
            raise OSError("disco lleno")

    sched.add(
        PeriodicTask("backup", lambda: timedelta(hours=24), lambda: True, backup, run_on_start=True)
    )
    t0 = datetime(2026, 11, 16, 12, tzinfo=UTC)
    assert sched.tick(t0) == []
    assert sched.errors()["backup"]["error"] == "disco lleno"
    assert sched.tick(t0 + RETRY_AFTER - timedelta(minutes=1)) == []  # aún no
    state["fail"] = False
    assert sched.tick(t0 + RETRY_AFTER) == ["backup"]  # 15 min después, no 24 h
    assert sched.errors() == {}


def test_backups_previos_a_actualizar_no_se_acumulan(tmp_path):
    from oraqlo.api.app import prune_backups

    for i in range(6):
        for prefix in ("pre-0.5.", "daily-2026111"):
            f = tmp_path / f"{prefix}{i}.db"
            f.write_text("x")
            os.utime(f, (time.time() + i, time.time() + i))
    prune_backups(tmp_path, daily=2, pre_update=3)
    assert sorted(f.name for f in tmp_path.glob("pre-*.db")) == [
        "pre-0.5.3.db",
        "pre-0.5.4.db",
        "pre-0.5.5.db",
    ]
    assert sorted(f.name for f in tmp_path.glob("daily-*.db")) == [
        "daily-20261114.db",
        "daily-20261115.db",
    ]


def test_jobs_viejos_se_borran_con_sus_eventos(tmp_path):
    store = Store(tmp_path / "o.db")
    db = store.db
    with db.tx() as conn:
        for job_id, status, age in (
            ("viejo", "done", 90),
            ("reciente", "done", 5),
            ("corriendo", "running", 90),
        ):
            conn.execute(
                "INSERT INTO jobs (id, kind, status, created_at, finished_at) VALUES (?, 'practice', ?, "
                "datetime('now', ?), datetime('now', ?))",
                (job_id, status, f"-{age} days", f"-{age} days"),
            )
            conn.execute(
                "INSERT INTO job_events (job_id, seq, type) VALUES (?, 1, 'log')", (job_id,)
            )
    assert db.prune_jobs(days=60) == 1
    left = {r[0] for r in db.query("SELECT id FROM jobs")}
    assert left == {"reciente", "corriendo"}  # lo que sigue en curso nunca se toca
    assert {r[0] for r in db.query("SELECT job_id FROM job_events")} == left


def test_migracion_que_falla_se_deshace(tmp_path, monkeypatch):
    from oraqlo.store import db as dbmod

    database = dbmod.Database(tmp_path / "o.db")
    version = database.version
    monkeypatch.setattr(
        dbmod, "MIGRATIONS", [*dbmod.MIGRATIONS, "CREATE TABLE nueva (x); SELECT * FROM no_existe;"]
    )
    with pytest.raises(Exception, match="no_existe"):
        database.migrate()
    assert not database.conn.in_transaction
    assert database.version == version
    assert not database.query("SELECT name FROM sqlite_master WHERE name = 'nueva'")


def test_practica_sin_resultados_y_con_errores_es_un_fallo(tmp_path, monkeypatch):
    from oraqlo import autopractice
    from oraqlo.config import load_config
    from oraqlo.services.core import OraqloService, PracticeFailed

    service = OraqloService(
        load_config(tmp_path / "none.toml", home=tmp_path), Store(tmp_path / "o.db")
    )
    failing = autopractice.PracticeReport(errors=["Polymarket no responde"])
    monkeypatch.setattr(autopractice.PracticeEngine, "run_once", lambda self, **kw: failing)
    with pytest.raises(PracticeFailed, match="Polymarket no responde"):
        service.practice(research=False)


def test_planificador_no_inventa_ejecuciones(tmp_path):
    """Armar el temporizador o fallar no cuenta como «última ejecución»."""
    store = Store(tmp_path / "o.db")
    sched = Scheduler(store.settings)
    state = {"fail": True}

    def action():
        if state["fail"]:
            raise OSError("caído")

    sched.add(PeriodicTask("t", lambda: timedelta(hours=2), lambda: True, action))
    t0 = datetime(2026, 11, 16, 12, tzinfo=UTC)
    assert sched.tick(t0) == []  # solo se arma
    assert sched.last_run("t") is None
    assert sched.next_run("t") == t0 + timedelta(hours=2)
    assert sched.tick(t0 + timedelta(hours=2)) == []  # falla
    assert sched.last_run("t") == t0 + timedelta(hours=2)  # sí corrió (y falló)
    assert sched.next_run("t") == t0 + timedelta(hours=2) + RETRY_AFTER
    state["fail"] = False
    assert sched.tick(t0 + timedelta(hours=2) + RETRY_AFTER) == ["t"]
    assert sched.next_run("t") == t0 + timedelta(hours=4) + RETRY_AFTER


def test_marcar_a_mano_reinicia_el_temporizador(tmp_path):
    store = Store(tmp_path / "o.db")
    sched = Scheduler(store.settings)
    sched.add(PeriodicTask("t", lambda: timedelta(hours=1), lambda: True, lambda: None))
    t0 = datetime(2026, 11, 16, 12, tzinfo=UTC)
    sched.tick(t0)
    sched.mark("t", t0 + timedelta(minutes=50))
    assert sched.tick(t0 + timedelta(minutes=61)) == []  # la manual reinició la cuenta
    assert sched.tick(t0 + timedelta(minutes=111)) == ["t"]
