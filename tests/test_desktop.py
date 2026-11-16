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


def test_utilidades_de_version_y_recarga():
    assert desktop.needs_restart("0.7.0", "0.8.0")
    assert not desktop.needs_restart("0.8.0", "0.8.0")
    assert not desktop.needs_restart("0.7.0", None)  # servidor caído: no es una versión nueva
    assert not desktop.needs_restart("0.0.0+unknown", "0.8.0")  # desde el árbol de fuentes
    # Recargar conserva la pantalla y sigue al servidor si cambió de puerto.
    assert desktop.reload_url("http://127.0.0.1:8766/ajustes?x=1#busqueda-web", 8770) == (
        "http://127.0.0.1:8770/ajustes?x=1#busqueda-web"
    )
    assert desktop.reload_url(None, 8766) == "http://127.0.0.1:8766/"


def test_abrir_otra_vez_recarga_la_pagina(tmp_path, monkeypatch):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "port.txt").write_text("8766", encoding="utf-8")
    app = desktop.DesktopApp(tmp_path, hidden=True)
    loaded: list[str] = []

    class Window:
        def get_current_url(self):
            return "http://127.0.0.1:8766/objetivos"

        def load_url(self, url):
            loaded.append(url)
            app.quitting = True  # una sola vuelta del bucle

        def show(self):
            pass

        def restore(self):
            pass

    app.window = Window()
    (tmp_path / "app" / "open-path.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    app.watch_open_requests()
    assert loaded == ["http://127.0.0.1:8766/objetivos"]
    assert app.hidden is False


def test_si_el_servidor_ya_es_otra_version_relanza_la_ventana(tmp_path, monkeypatch):
    launcher = _install(tmp_path, version="9.9.9")
    app = desktop.DesktopApp(tmp_path, hidden=True)
    spawned: list[list[str]] = []
    monkeypatch.setattr(desktop, "server_version", lambda port: "9.9.9")
    monkeypatch.setattr(desktop, "__version__", "0.7.0")
    monkeypatch.setattr(desktop.DesktopApp, "quit", lambda self: setattr(self, "quitting", True))

    assert app.check_version(spawn=lambda cmd, **kw: spawned.append(cmd)) is True
    cmd = spawned[0]
    assert str(launcher.python("9.9.9")) == cmd[0]
    assert cmd[-2:] == ["--after-update", "--hidden"]  # sigue en la bandeja si lo estaba
    assert app.quitting

    # Misma versión instalada que la ventana: no hay nada a lo que relanzar.
    spawned.clear()
    monkeypatch.setattr(desktop, "__version__", "9.9.9")
    assert (
        desktop.DesktopApp(tmp_path).check_version(spawn=lambda c, **k: spawned.append(c)) is False
    )
    assert spawned == []


# ── el servidor nuevo reemplaza la ventana vieja ─────────────────────────


def _rows(home, version="0.5.1", pid=100, child=101, extra=()):
    exe = str(home / "app" / "versions" / version / "Scripts" / "pythonw.exe")
    cmd = f"{exe} -m oraqlo.desktop --home {home}"
    return [
        {"ProcessId": pid, "ParentProcessId": 7, "CommandLine": cmd},
        {"ProcessId": child, "ParentProcessId": pid, "CommandLine": cmd},  # intérprete real
        {
            "ProcessId": 300,
            "ParentProcessId": 7,
            "CommandLine": f"{exe} -m oraqlo.cli serve --port 8766",
        },
        *extra,
    ]


def test_detecta_las_ventanas_de_este_home_y_su_version(tmp_path):
    from oraqlo.windows_handover import find_windows

    other_home = tmp_path / "otro"
    rows = _rows(tmp_path) + _rows(other_home, version="0.9.0", pid=200, child=201)
    rows.append({"ProcessId": 400, "ParentProcessId": 1, "CommandLine": None})
    windows = find_windows(rows, tmp_path)
    assert len(windows) == 1
    w = windows[0]
    assert (w.root, set(w.pids), w.version) == (100, {100, 101}, "0.5.1")


def _handover(tmp_path, rows, visible=False, current="0.10.0"):
    from oraqlo.windows_handover import replace_old_windows

    _install(tmp_path, version=current)  # current.txt y la ventana de la versión activa
    killed, launched, notes = [], [], []
    replaced = replace_old_windows(
        tmp_path,
        current,
        processes=lambda: rows,
        visible=lambda pids: visible,
        kill=killed.append,
        launch=launched.append,
        note=notes.append,
    )
    return replaced, killed, launched, notes


def test_cierra_la_vieja_y_abre_la_nueva_escondida_si_estaba_en_la_bandeja(tmp_path):
    replaced, killed, launched, notes = _handover(tmp_path, _rows(tmp_path), visible=False)
    assert replaced == ["0.5.1"] and killed == [100]  # el árbol, desde el lanzador
    cmd = launched[0]
    assert cmd[1:3] == ["-m", "oraqlo.desktop"] and cmd[-2:] == ["--after-update", "--hidden"]
    assert "0.10.0" in cmd[0]
    assert "ventana 0.5.1 reemplazada por 0.10.0 (en la bandeja)" in notes


def test_si_estaba_a_la_vista_la_nueva_tambien(tmp_path):
    _replaced, _killed, launched, _notes = _handover(tmp_path, _rows(tmp_path), visible=True)
    assert launched[0][-1] == "--after-update"


def test_nada_que_hacer_con_la_misma_version_o_sin_ventana(tmp_path):
    rows = _rows(tmp_path, version="0.10.0")
    assert _handover(tmp_path, rows) == ([], [], [], [])
    assert _handover(tmp_path / "b", []) == ([], [], [], [])


def test_si_falla_la_consulta_no_tumba_nada(tmp_path):
    from oraqlo.windows_handover import replace_old_windows

    def boom():
        raise OSError("powershell no disponible")

    assert replace_old_windows(tmp_path, "0.10.0", processes=boom) == []
