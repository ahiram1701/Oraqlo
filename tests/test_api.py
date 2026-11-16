"""API web: auth Tailscale, jobs + SSE, ledger, objetivos, práctica, casos (offline)."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from oraqlo.config import load_config
from oraqlo.llm.base import LLMProvider, LLMResponse
from oraqlo.services.core import OraqloService
from oraqlo.store import Store

ROOT = Path(__file__).resolve().parent.parent


class RoleAwareFake(LLMProvider):
    """Responde según el prompt de sistema: el orden de las llamadas no importa."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def chat(self, messages, model, temperature=0.7, json_mode=False, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("proveedor caído a propósito")
        system = messages[0].content
        if "p_logro" in system:
            out = {"p_logro": 0.55, "comment": "va bien", "replan_recommended": False}
        elif '"outcomes"' in system:
            out = {"outcomes": {"sube": 0.7, "baja": 0.3}, "assumptions": ["a1"], "rationale": "r"}
        elif "Red Team" in system:
            out = {"objections": ["riesgo de ejecución"]}
        elif "p_exito" in system:
            out = {"p_exito": 0.7, "p_objeciones": {"riesgo de ejecución": 0.2}}
        elif '"decision"' in system:
            out = {
                "decision": "emitir",
                "confidence": 0.7,
                "rationale": "ok",
                "review_triggers": [],
            }
        else:
            return LLMResponse(content="Plan sólido con supuestos claros.", model=model)
        return LLMResponse(content=json.dumps(out), model=model)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from oraqlo.config import AUTO_PROVIDERS

    for _, var in AUTO_PROVIDERS:
        # setenv+delenv: monkeypatch restaura el estado original aunque la app
        # escriba la variable durante el test (claves desde Ajustes).
        monkeypatch.setenv(var, "")
        monkeypatch.delenv(var)
    config = load_config(tmp_path / "none.toml", home=tmp_path)
    config.updates.mode = "off"  # los tests nunca consultan GitHub
    service = OraqloService(config, Store(tmp_path / "o.db"))
    fake = RoleAwareFake()
    monkeypatch.setattr(service.registry, "get", lambda name, effort=None: fake)
    from oraqlo.api.app import create_app
    from oraqlo.services.network import TailscaleServe

    # Sin Tailscale real: los tests no tocan la configuración del equipo.
    app = create_app(service, background=False, tailscale=TailscaleServe(exe=""))
    with TestClient(app) as client:
        yield client, service, app, fake


def wait_job(client, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"El job {job_id} no terminó")


def sse(text: str) -> list[tuple[str | None, str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(
            line.split(": ", 1)
            for line in block.splitlines()
            if ": " in line and not line.startswith(":")
        )
        if "event" in fields:
            events.append((fields.get("id"), fields["event"], json.loads(fields["data"])))
    return events


# ── sistema y auth ───────────────────────────────────────────────────────


def test_health_y_me_local(env):
    client, *_ = env
    h = client.get("/api/health").json()
    assert h["app"] == "oraqlo" and h["status"] == "ok" and h["db_version"] >= 1
    assert client.get("/api/me").json() == {"login": None, "name": None, "via": "local"}


def test_auth_tailscale(env, monkeypatch):
    client, service, *_ = env
    proxied = {"X-Forwarded-For": "100.64.0.7"}
    assert client.get("/api/health", headers=proxied).status_code == 401
    ok = client.get("/api/me", headers={**proxied, "Tailscale-User-Login": "yo@github"})
    assert ok.status_code == 200 and ok.json()["via"] == "tailscale"
    funnel = {"Tailscale-Funnel-Request": "?1", "Tailscale-User-Login": "x"}
    assert client.get("/api/health", headers=funnel).status_code == 403
    monkeypatch.setattr(service.config.server, "allowed_users", ["yo@github"])
    other = client.get("/api/health", headers={**proxied, "Tailscale-User-Login": "otro@github"})
    assert other.status_code == 403


# ── jobs ─────────────────────────────────────────────────────────────────


def test_pregunta_rapida_job_sse_e_informe(env):
    client, service, *_ = env
    r = client.post("/api/ask", json={"question": "¿Sube el mercado?", "research": False})
    assert r.status_code == 202
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["forecast"]["outcomes"] == {"sube": 0.7, "baja": 0.3}
    assert len(service.store.ledger.open_forecasts) == 1

    stream = client.get(f"/api/jobs/{job['id']}/events").text
    events = sse(stream)
    kinds = [k for _, k, _ in events]
    assert "forecast" in kinds and kinds[-1] == "end"
    assert events[-1][2]["status"] == "done"
    # Reanudar desde el último id recibido: solo llega el cierre.
    last_seq = [i for i, k, _ in events if i][-1]
    resumed = sse(
        client.get(f"/api/jobs/{job['id']}/events", headers={"Last-Event-ID": last_seq}).text
    )
    assert [k for _, k, _ in resumed] == ["end"]

    run_id = job["run_id"]
    assert client.get("/api/runs").json()[0]["id"] == run_id
    report = client.get(f"/api/runs/{run_id}/report.md")
    assert report.status_code == 200 and "Informe estratégico" in report.text
    assert "attachment" in report.headers["content-disposition"]


def test_ciclo_con_caso_emite_debate(env):
    client, *_ = env
    case = json.loads((ROOT / "cases" / "cafeterias.json").read_text(encoding="utf-8"))
    job = wait_job(client, client.post("/api/cases/run", json={"case": case}).json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["verdict"]["decision"] == "emitir"
    turns = [
        d["role"]
        for _, k, d in sse(client.get(f"/api/jobs/{job['id']}/events").text)
        if k == "turn"
    ]
    assert turns == ["estratega", "red_team", "oraculo", "juez"]


def test_caso_invalido_422(env):
    client, *_ = env
    assert client.post("/api/cases/run", json={"case": {"question": ""}}).status_code == 422
    v = client.post("/api/cases/validate", json={"case": {"question": "x"}}).json()
    assert v["ok"] is False and v["error"]


def test_job_fallido_reporta_error(env):
    client, _, _, fake = env
    fake.fail = True
    job = wait_job(
        client,
        client.post("/api/ask", json={"question": "¿pasa algo?", "research": False}).json()[
            "job_id"
        ],
    )
    assert job["status"] == "failed" and "caído" in job["error"]
    kinds = [k for _, k, _ in sse(client.get(f"/api/jobs/{job['id']}/events").text)]
    # "job_error", no "error": ese nombre es el evento nativo de EventSource.
    assert kinds[-2:] == ["job_error", "end"]


def test_recover_marca_interrumpidos(env):
    client, service, app, _ = env
    with service.store.db.tx() as conn:
        conn.execute("INSERT INTO jobs (id, kind, status) VALUES ('j1', 'forecast', 'running')")
    assert app.state.runner.recover() == 1
    job = client.get("/api/jobs/j1").json()
    assert job["status"] == "failed" and "reinició" in job["error"]


# ── ledger y calibración ─────────────────────────────────────────────────


def test_ledger_resolver_descartar_y_calibracion(env):
    client, *_ = env
    ids = []
    for q in ("¿A?", "¿B?", "¿C?"):
        job = wait_job(
            client,
            client.post("/api/ask", json={"question": q, "research": False}).json()["job_id"],
        )
        ids.append(job["result"]["forecast"]["id"])
    r = client.post(f"/api/ledger/{ids[0]}/resolve", json={"outcome": "SUBE "})
    assert r.status_code == 200 and r.json()["resolved"][0]["actual_outcome"] == "sube"
    assert client.delete(f"/api/ledger/open/{ids[1]}").status_code == 204
    assert client.post("/api/ledger/nope/resolve", json={"outcome": "x"}).status_code == 404
    view = client.get("/api/ledger").json()
    assert [f["id"] for f in view["open"]] == [ids[2]]
    assert view["resolved"][0]["brier"] == pytest.approx(0.18)
    cal = client.get("/api/calibration").json()
    assert cal["n"] == 1 and cal["sources"][0]["brier"] == pytest.approx(0.18)
    assert sum(b["n"] for b in cal["reliability"]) == 2
    assert client.delete(f"/api/ledger/resolved/{ids[0]}").status_code == 204


# ── objetivos ────────────────────────────────────────────────────────────


def test_objetivos_checkin_hitos_y_cierre(env):
    from oraqlo.goaltrack import GoalTracker
    from test_goaltrack import _plan

    client, service, *_ = env
    tracked = GoalTracker(service.store.goals, service.store.ledger).track(_plan())
    goals = client.get("/api/goals").json()
    assert goals[0]["id"] == tracked.id and goals[0]["trajectory"] == [0.6]

    job = wait_job(
        client,
        client.post(f"/api/goals/{tracked.id}/checkin", json={"note": "semana 1 ok"}).json()[
            "job_id"
        ],
    )
    assert job["status"] == "done", job
    assert job["result"]["goal"]["trajectory"] == [0.6, 0.55]

    m = client.put(f"/api/goals/{tracked.id}/milestones/0", json={"status": "cumplido"})
    assert m.status_code == 200 and m.json()["milestones"][0]["status"] == "cumplido"
    assert (
        client.put(f"/api/goals/{tracked.id}/milestones/0", json={"status": "raro"}).status_code
        == 422
    )

    closed = client.post(f"/api/goals/{tracked.id}/close", json={"achieved": True}).json()
    assert closed["goal"]["status"] == "logrado"
    assert client.delete(f"/api/goals/{tracked.id}").status_code == 204
    assert client.get(f"/api/goals/{tracked.id}").status_code == 404


# ── práctica y planificador ──────────────────────────────────────────────


def test_practica_ajustes_persisten(env):
    client, service, *_ = env
    state = client.put("/api/practice", json={"auto": True, "interval_min": 30}).json()
    assert state["auto"] is True and state["interval_min"] == 30
    assert service.store.settings.get("practice_auto") is True


def test_scheduler_arma_y_dispara_practica(env, monkeypatch):
    client, _service, app, _ = env
    submitted = []
    monkeypatch.setattr(
        app.state.runner, "submit", lambda kind, params=None: submitted.append(kind)
    )
    client.put("/api/practice", json={"auto": True, "interval_min": 30})
    scheduler = app.state.scheduler
    t0 = datetime(2026, 11, 15, 10, tzinfo=UTC)
    ran = lambda t: [n for n in scheduler.tick(t) if n == "practice"]
    assert ran(t0) == []  # primera vez: arma el temporizador, no gasta llamadas
    assert ran(t0 + timedelta(minutes=10)) == []
    assert ran(t0 + timedelta(minutes=31)) == ["practice"]
    assert submitted == ["practice"]
    client.put("/api/practice", json={"auto": False})
    assert ran(t0 + timedelta(hours=5)) == []


# ── casos y estáticos ────────────────────────────────────────────────────


def test_casos_crud(env):
    client, *_ = env
    case = json.loads((ROOT / "cases" / "cafeterias.json").read_text(encoding="utf-8"))
    assert client.put("/api/cases/c1", json={"name": "Cafeterías", "case": case}).status_code == 200
    assert client.get("/api/cases").json()[0]["name"] == "Cafeterías"
    assert client.get("/api/cases/c1").json()["data"]["question"] == case["question"]
    assert client.delete("/api/cases/c1").status_code == 204
    assert client.get("/api/cases/c1").status_code == 404
    assert "question" in client.get("/api/cases/template").json()


def test_spa_y_rutas_desconocidas(env):
    client, *_ = env
    assert client.get("/api/no-existe").status_code == 404
    assert client.get("/objetivos").status_code == 200


def test_el_html_dice_de_que_version_es(env):
    """La interfaz guardada por el service worker sabe su versión y nota si el
    servidor ya es otra (tras una actualización automática)."""
    from oraqlo import __version__
    from oraqlo.api.app import STATIC_DIR

    if not (STATIC_DIR / "index.html").exists():
        pytest.skip("frontend sin compilar")
    client, *_ = env
    for route in ("/", "/objetivos", "/index.html"):
        r = client.get(route)
        assert f'<meta name="oraqlo-version" content="{__version__}"' in r.text
        assert r.headers["cache-control"] == "no-cache"


def test_proveedores_sin_exponer_claves(env, monkeypatch):
    client, *_ = env
    monkeypatch.setenv("GROQ_API_KEY", "secreto-que-no-debe-salir")
    body = client.get("/api/providers").text
    assert "secreto" not in body
    groq = next(p for p in json.loads(body)["providers"] if p["name"] == "groq")
    assert groq["key_present"] is True


# ── notificaciones push ──────────────────────────────────────────────────

SUB = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "k", "auth": "a"}}


def test_push_claves_suscripcion_y_envio(env, monkeypatch):
    import pywebpush

    client, _service, _app, _ = env
    key = client.get("/api/push/key").json()["public_key"]
    assert len(key) > 80 and client.get("/api/push/key").json()["public_key"] == key  # estable
    assert client.post("/api/push/test").status_code == 409  # nadie suscrito
    assert client.post("/api/push/subscribe", json={"endpoint": "x", "keys": {}}).status_code == 422
    assert client.post("/api/push/subscribe", json=SUB).json() == {"devices": 1}

    sent = []
    monkeypatch.setattr(pywebpush, "webpush", lambda **kw: sent.append(kw))
    assert client.post("/api/push/test").json() == {"delivered": 1}
    payload = json.loads(sent[0]["data"])
    assert payload["title"] == "Oraqlo" and payload["url"] == "/ajustes"
    assert sent[0]["vapid_claims"]["sub"].startswith("https://")


def test_push_borra_suscripciones_muertas(env, monkeypatch):
    import pywebpush

    client, _, app, _ = env
    client.post("/api/push/subscribe", json=SUB)

    def gone(**kw):
        raise pywebpush.WebPushException("gone", response=type("R", (), {"status_code": 410})())

    monkeypatch.setattr(pywebpush, "webpush", gone)
    assert app.state.push.send("t", "b") == 0
    assert app.state.push.count() == 0


def test_aviso_al_terminar_un_job(env, monkeypatch):
    import pywebpush

    client, *_ = env
    client.post("/api/push/subscribe", json=SUB)
    sent = []
    monkeypatch.setattr(pywebpush, "webpush", lambda **kw: sent.append(json.loads(kw["data"])))
    wait_job(
        client,
        client.post("/api/ask", json={"question": "¿Lloverá?", "research": False}).json()["job_id"],
    )
    deadline = time.monotonic() + 3
    while not sent and time.monotonic() < deadline:
        time.sleep(0.05)
    assert sent and sent[0]["title"] == "Previsión lista"
    assert "¿Lloverá?: sube 70%" in sent[0]["body"] and sent[0]["url"].startswith("/historial/")


def test_aviso_de_hito_vencido_una_sola_vez(env, monkeypatch):
    import pywebpush

    from oraqlo.goaltrack import GoalTracker, TrackedMilestone
    from test_goaltrack import _plan

    client, service, app, _ = env
    tracked = GoalTracker(service.store.goals, service.store.ledger).track(_plan())
    goal = service.store.goals.get(tracked.id)
    goal.milestones.append(TrackedMilestone(text="vencido", target_date="2020-01-01", signal="s"))
    service.store.goals.update(goal)
    client.post("/api/push/subscribe", json=SUB)
    sent = []
    monkeypatch.setattr(pywebpush, "webpush", lambda **kw: sent.append(json.loads(kw["data"])))
    scheduler = app.state.scheduler
    now = datetime(2026, 11, 15, tzinfo=UTC)
    assert "milestones" in scheduler.tick(now)
    assert "milestones" in scheduler.tick(now + timedelta(hours=7))
    assert len(sent) == 1 and "vencido" in sent[0]["body"]


# ── actualizaciones y backups ────────────────────────────────────────────


def test_backup_diario_rota(env):
    _client, service, app, _ = env
    backups = service.config.backups_dir
    backups.mkdir(parents=True, exist_ok=True)
    for i in range(9):
        (backups / f"daily-2026010{i}.db").write_bytes(b"x")
    assert "backup" in app.state.scheduler.tick(datetime(2026, 11, 15, tzinfo=UTC))
    dailies = sorted(backups.glob("daily-*.db"))
    assert len(dailies) == 7 and dailies[-1].stat().st_size > 1  # la de hoy es una base real


def test_health_informa_actualizaciones_y_copia_no_gestionada(env, monkeypatch):

    client, _service, app, _ = env
    upd = client.get("/api/health").json()["update"]
    assert upd["managed"] is False and upd["mode"] == "off"
    monkeypatch.setattr(
        app.state.updater,
        "fetch",
        lambda url: {
            "tag_name": "v99.0.0",
            "html_url": "x",
            "assets": [{"name": "oraqlo-99.0.0-py3-none-any.whl", "browser_download_url": "u"}],
        },
    )
    r = client.post("/api/update/check").json()
    assert r["status"] == "unmanaged" and "git" in r["detail"]
    assert client.get("/api/health").json()["update"]["available"] == "99.0.0"
    assert client.post("/api/update/apply").status_code == 409


def test_health_no_anuncia_la_version_que_ya_corre(env):
    from oraqlo import __version__

    client, service, *_ = env
    service.store.settings.set("update.available", __version__)
    service.store.settings.set("update.staged", "0.0.1")
    upd = client.get("/api/health").json()["update"]
    assert upd["available"] is None and upd["staged"] is None


def test_instalacion_gestionada_activa_cuando_no_hay_trabajos(tmp_path, monkeypatch):
    from oraqlo.api import app as app_module
    from oraqlo.api.app import create_app

    monkeypatch.setattr(app_module, "is_managed", lambda layout: True)
    config = load_config(tmp_path / "none.toml", home=tmp_path)
    service = OraqloService(config, Store(config.db_path))
    app = create_app(service, background=False)
    activated = []
    monkeypatch.setattr(app.state.updater, "activate", lambda v, d: activated.append(v))
    monkeypatch.setattr(app.state.updater.layout, "prune", lambda: [])
    with TestClient(app):
        service.store.settings.set("update.staged", "9.9.9")
        with service.store.db.tx() as conn:
            conn.execute(
                "INSERT INTO jobs (id, kind, status) VALUES ('busy', 'forecast', 'running')"
            )
        app.state.apply_staged()
        assert activated == [] and not app.state.restart_requested  # pospuesto: hay un análisis
        with service.store.db.tx() as conn:
            conn.execute("UPDATE jobs SET status = 'done'")
        app.state.apply_staged()
        assert activated == ["9.9.9"] and app.state.restart_requested
        assert service.store.settings.get("update.pending_notice") == "9.9.9"
        assert service.store.settings.get("update.staged") is None


# ── puerto y tailnet ─────────────────────────────────────────────────────


def _managed_app(tmp_path, monkeypatch, ts):
    import oraqlo.api.app as app_module

    config = load_config(tmp_path / "none.toml", home=tmp_path)
    config.updates.mode = "off"
    service = OraqloService(config, Store(tmp_path / "o.db"))
    monkeypatch.setattr(app_module, "is_managed", lambda layout: True)
    app = app_module.create_app(service, background=False, tailscale=ts)
    app.state.port = 8765
    return app


def test_server_info_y_cambio_de_puerto(tmp_path, monkeypatch):
    import oraqlo.api.app as app_module
    from test_network import fake_tailscale

    ts, _calls = fake_tailscale()
    app = _managed_app(tmp_path, monkeypatch, ts)
    (tmp_path / "app").mkdir(exist_ok=True)
    (tmp_path / "app" / "tailscale.txt").write_text("8443", encoding="utf-8")
    with TestClient(app) as client:
        info = client.get("/api/server").json()
        assert info["port"] == 8765 and info["managed"] is True
        assert info["tailscale"] == {
            "installed": True,
            "state": "running",
            "enabled": True,
            "https_port": 8443,
            "url": "https://pc.tail1.ts.net:8443",
            "serving_port": 8765,
        }
        assert client.put("/api/server/port", json={"port": 80}).status_code == 422
        same = client.put("/api/server/port", json={"port": 8765}).json()
        assert same["restarting"] is False

        monkeypatch.setattr(app_module, "port_in_use", lambda port: port == 8766)
        busy = client.put("/api/server/port", json={"port": 8766})
        assert busy.status_code == 409 and "otra aplicación" in busy.json()["detail"]

        ok = client.put("/api/server/port", json={"port": 8770}).json()
        assert ok["restarting"] is True and ok["local_url"] == "http://127.0.0.1:8770"
        assert (tmp_path / "app" / "port.txt").read_text() == "8770"
        assert app.state.restart_requested is True


def test_al_arrancar_tailscale_sigue_al_puerto_real(tmp_path, monkeypatch):
    from test_network import fake_tailscale

    ts, _calls = fake_tailscale()  # publica 8765 en :8443
    app = _managed_app(tmp_path, monkeypatch, ts)
    app.state.port = 8767  # el lanzador saltó de puerto
    (tmp_path / "app").mkdir(exist_ok=True)
    (tmp_path / "app" / "tailscale.txt").write_text("8443", encoding="utf-8")
    with TestClient(app):
        deadline = time.monotonic() + 5
        while ts.web != {8443: 8767} and time.monotonic() < deadline:
            time.sleep(0.05)
    assert ts.web == {8443: 8767}


def test_al_arrancar_deja_la_raiz_que_ocupaba_una_version_vieja(tmp_path, monkeypatch):
    from test_network import fake_tailscale

    ts, _calls = fake_tailscale(web={443: 8765})  # 0.5 y anteriores: en la raíz
    app = _managed_app(tmp_path, monkeypatch, ts)
    (tmp_path / "app").mkdir(exist_ok=True)
    (tmp_path / "app" / "tailscale.txt").write_text("on", encoding="utf-8")
    with TestClient(app):
        deadline = time.monotonic() + 5
        while ts.web != {8443: 8765} and time.monotonic() < deadline:
            time.sleep(0.05)
    assert ts.web == {8443: 8765}
    assert (tmp_path / "app" / "tailscale.txt").read_text() == "8443"


def test_cambiar_puerto_en_copia_no_gestionada(env):
    client, *_ = env
    r = client.put("/api/server/port", json={"port": 8770})
    assert r.status_code == 409 and "desarrollo" in r.json()["detail"]


def test_push_real_firma_y_cifra_sin_mock_de_webpush(tmp_path, monkeypatch):
    """Regresión: la clave VAPID se pasaba como texto PEM y TODOS los envíos fallaban
    ("Could not deserialize key data"). Aquí corre el webpush real; solo se
    intercepta la llamada HTTP final al servicio de push."""
    import base64
    import os
    import types

    import pywebpush
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    from oraqlo.services.push import PushService
    from oraqlo.store import Store

    store = Store(tmp_path / "o.db")
    push = PushService(store.db, store.settings)
    device = ec.generate_private_key(ec.SECP256R1())
    raw = device.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    push.subscribe(
        {
            "endpoint": "https://web.push.apple.com/abc",
            "keys": {"p256dh": b64(raw), "auth": b64(os.urandom(16))},
        },
        "yo@github",
    )
    posted: list[dict] = []

    class Ok:
        status_code = 201
        headers = types.MappingProxyType({})
        text = ""

    def fake_post(url, data=None, headers=None, timeout=None, **kw):
        posted.append({"url": url, "headers": headers, "data": data})
        return Ok()

    monkeypatch.setattr(pywebpush.requests, "post", fake_post)
    assert push.send_web("Hola", "prueba", "/ajustes") == (1, None)
    sent = posted[0]
    assert sent["url"] == "https://web.push.apple.com/abc"
    assert sent["headers"]["Authorization"].startswith("vapid t=")  # JWT firmado
    assert sent["headers"]["Content-Encoding"] == "aes128gcm" and sent["data"]


def test_prueba_de_push_dice_la_verdad(env, monkeypatch):
    client, _service, app, _fake = env

    class Toast:
        available = True

        def send(self, *a):
            return True

    app.state.push.desktop = Toast()  # el toast de Windows ya no cuenta como entregado
    r = client.post("/api/push/test")
    assert r.status_code == 409 and "No hay dispositivos" in r.json()["detail"]
    status = client.get("/api/push/status", params={"endpoint": "x"}).json()
    assert status == {"subscribed": False, "devices": 0, "last_error": None}


def test_avisos_de_jobs_dicen_de_que_y_llevan_a_su_pantalla():
    from oraqlo.services.push import job_notification, practice_alert

    failed_checkin = {
        "kind": "checkin",
        "status": "failed",
        "error": "sin clave",
        "params": {"goal_id": "g1", "note": "corrí 5 km"},
    }
    _title, body, url = job_notification(failed_checkin)
    assert url == "/objetivos/g1" and body.startswith("corrí 5 km — sin clave")
    case = {
        "kind": "case",
        "status": "failed",
        "error": "x",
        "params": {"case": {"question": "¿Abro tienda?"}},
    }
    assert (
        job_notification(case)[1].startswith("¿Abro tienda?")
        and job_notification(case)[2] == "/caso"
    )
    failed = {"kind": "practice", "status": "failed", "error": "Polymarket caído"}
    assert practice_alert(failed, 1) is None
    assert practice_alert(failed, 2)[2] == "/practica"
    assert practice_alert(failed, 3) is None  # una sola vez por racha


def test_historial_admite_mas_de_50_y_valida_el_limite(env):
    client, service, *_ = env
    for i in range(60):
        service._save_run("forecast", f"Pregunta {i}", {"kind": "forecast"})
    assert len(client.get("/api/runs").json()) == 50  # por defecto, como antes
    assert len(client.get("/api/runs?limit=100").json()) == 60
    assert client.get("/api/runs?limit=0").status_code == 422
    assert client.get("/api/runs?limit=999999").status_code == 422
