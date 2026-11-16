"""Puerto local y sincronización de `tailscale serve`."""

from __future__ import annotations

import json
import socket
import subprocess

from oraqlo.services.network import (
    TailscaleServe,
    port_in_use,
    read_port_file,
    tailscale_enabled,
    write_port_file,
)


def _done(stdout="", code=0):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr="")


SERVE = {
    "TCP": {"443": {"HTTPS": True}},
    "Web": {"pc.tail1.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}}},
}
STATUS = {"Self": {"DNSName": "pc.tail1.ts.net."}}


def fake_tailscale(serve=SERVE):
    calls: list[list[str]] = []

    def run(cmd):
        calls.append(cmd[1:])
        if cmd[1:3] == ["serve", "status"]:
            return _done(json.dumps(serve))
        if cmd[1] == "status":
            return _done(json.dumps(STATUS))
        return _done()

    return TailscaleServe(exe="tailscale", run=run), calls


def test_lee_url_y_puerto_publicado():
    ts, _ = fake_tailscale()
    assert ts.url() == "https://pc.tail1.ts.net"
    assert ts.serving_port() == 8765


def test_ensure_solo_cambia_si_apunta_a_otro_puerto():
    ts, calls = fake_tailscale()
    assert ts.ensure(8765) is False
    assert ["serve", "--bg", "8765"] not in calls
    assert ts.ensure(8770) is True
    assert calls[-1] == ["serve", "--bg", "8770"]


def test_sin_tailscale_no_hace_nada():
    ts = TailscaleServe(exe="", run=lambda cmd: _done())
    assert not ts.installed and ts.url() is None and ts.ensure(8770) is False


def test_serve_vacio_o_json_roto():
    ts, _ = fake_tailscale(serve={})
    assert ts.serving_port() is None
    broken = TailscaleServe(exe="tailscale", run=lambda cmd: _done("no-json"))
    assert broken.serving_port() is None and broken.url() is None


def test_port_in_use_detecta_un_puerto_escuchando():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        assert port_in_use(s.getsockname()[1])


def test_archivos_de_puerto_y_tailscale(tmp_path):
    assert read_port_file(tmp_path) is None
    write_port_file(tmp_path, 8771)
    assert read_port_file(tmp_path) == 8771
    (tmp_path / "port.txt").write_text("80", encoding="utf-8")
    assert read_port_file(tmp_path) is None  # fuera de rango
    assert not tailscale_enabled(tmp_path)
    (tmp_path / "tailscale.txt").write_text("﻿on", encoding="utf-8")
    assert tailscale_enabled(tmp_path)
