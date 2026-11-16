"""App de escritorio: ventana nativa (vía el lanzador) y avisos de Windows."""

from __future__ import annotations

import json
import threading

from oraqlo import desktop
from oraqlo.launcher import Launcher, desktop_command, open_app
from test_api import env  # noqa: F401  (fixture compartida)

REMOTE = {"X-Forwarded-For": "100.64.0.2", "Tailscale-User-Login": "yo@github"}


def _install(tmp_path, version="0.5.0", with_desktop=True):
    app = tmp_path / "app"
    pkg = app / "versions" / version / "Lib" / "site-packages" / "oraqlo"
    pkg.mkdir(parents=True)
    if with_desktop:
        (pkg / "desktop.py").write_text("", encoding="utf-8")
    (app / "current.txt").write_text(version, encoding="utf-8")
    (app / "port.txt").write_text("8766", encoding="utf-8")
    return Launcher(tmp_path, sleep=lambda s: None, healthy=lambda p: p == 8766)


def test_abrir_usa_la_ventana_nativa_si_la_version_la_trae(tmp_path):
    launcher = _install(tmp_path)
    launched, opened = [], []

    def fake_desktop(cmd):
        launched.append(cmd)
        return True

    code = open_app(
        launcher, start=lambda: None, browse=opened.append, path="/ledger", desktop=fake_desktop
    )
    assert code == 0 and opened == []
    cmd = launched[0]
    assert cmd[1:4] == ["-m", "oraqlo.desktop", "--home"]
    assert cmd[-2:] == ["--path", "/ledger"]


def test_sin_ventana_o_si_falla_abre_el_navegador(tmp_path):
    old = _install(tmp_path / "vieja", with_desktop=False)
    assert desktop_command(old) is None
    opened: list[str] = []
    open_app(old, start=lambda: None, browse=opened.append, path="/ajustes", desktop=lambda c: True)
    assert opened == ["http://127.0.0.1:8766/ajustes"]

    broken = _install(tmp_path / "rota")
    opened.clear()
    open_app(broken, start=lambda: None, browse=opened.append, desktop=lambda c: False)
    assert opened == ["http://127.0.0.1:8766/"]


def test_utilidades_de_la_ventana(tmp_path):
    (tmp_path / "app").mkdir()
    assert desktop.read_port(tmp_path) == 8765
    (tmp_path / "app" / "port.txt").write_text("﻿8790", encoding="utf-8")
    assert desktop.read_port(tmp_path) == 8790
    assert desktop.app_url(8790, "objetivos/1") == "http://127.0.0.1:8790/objetivos/1"
    assert desktop.app_url(8790) == "http://127.0.0.1:8790/"

    assert desktop.load_geometry(tmp_path) == {"width": 1100, "height": 780}

    app = desktop.DesktopApp(tmp_path)
    app.on_resized(300, 900)
    app.on_moved(10, 20)
    desktop.save_geometry(tmp_path, app.geometry)
    assert json.loads((tmp_path / "app" / "window.json").read_text()) == {
        "width": 300,
        "height": 900,
        "x": 10,
        "y": 20,
    }
    # Nunca más pequeña que el mínimo, aunque se guardara así.
    assert desktop.load_geometry(tmp_path) == {"width": 400, "height": 900, "x": 10, "y": 20}


def test_cerrar_la_ventana_la_esconde_en_la_bandeja(tmp_path):
    (tmp_path / "app").mkdir()
    app = desktop.DesktopApp(tmp_path)

    class Window:
        width, height, x, y = 800, 600, 0, 0

        def __init__(self):
            self.hidden = threading.Event()

        def hide(self):
            self.hidden.set()

    class Tray:
        def __init__(self):
            self.notes: list[str] = []

        def notify(self, msg, title):
            self.notes.append(msg)

    app.window, app.tray = Window(), Tray()
    assert app.on_closing() is False and app.window.hidden.wait(2)
    assert app.on_closing() is False and len(app.tray.notes) == 1  # el aviso, una vez
    app.quitting = True
    assert app.on_closing() is True


class FakeNotifier:
    available = True

    def __init__(self):
        self.sent: list[tuple] = []

    def send(self, title, body, url="/"):
        self.sent.append((title, body, url))
        return True


def test_avisos_de_windows_se_activan_y_se_prueban(env):
    client, _service, app, _fake = env
    notifier = FakeNotifier()
    app.state.push.desktop = notifier
    assert client.get("/api/notify/windows").json() == {"supported": True, "enabled": True}

    assert client.post("/api/notify/windows/test").json() == {"ok": True}
    assert notifier.sent[-1][2] == "/ajustes"

    # El push normal también llega a Windows (sin suscripciones web).
    assert app.state.push.send("Hito vencido", "Revisa tu objetivo", url="/objetivos/1") == 1
    assert notifier.sent[-1] == ("Hito vencido", "Revisa tu objetivo", "/objetivos/1")

    off = client.put("/api/notify/windows", json={"enabled": False}).json()
    assert off == {"supported": True, "enabled": False}
    assert app.state.push.send("x", "y") == 0
    assert client.post("/api/notify/windows/test").status_code == 409
    assert (
        client.put("/api/notify/windows", json={"enabled": True}, headers=REMOTE).status_code == 403
    )


def test_sin_notificador_no_hay_avisos_de_windows(env):
    client, *_ = env
    assert client.get("/api/notify/windows").json() == {"supported": False, "enabled": False}


def test_notificador_fuera_de_windows_no_hace_nada(tmp_path, monkeypatch):
    from oraqlo.services import notify

    monkeypatch.setattr(notify.os, "name", "posix")
    assert notify.DesktopNotifier(tmp_path).send("a", "b") is False
