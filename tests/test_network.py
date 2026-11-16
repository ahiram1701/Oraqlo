"""Puerto local y publicación en la tailnet (`tailscale serve`)."""

from __future__ import annotations

import json
import socket
import subprocess

from oraqlo.services.network import (
    HTTPS_PORT,
    TailscaleServe,
    port_in_use,
    published_https,
    read_port_file,
    set_published_https,
    tailscale_enabled,
    write_port_file,
)

HOST = "pc.tail1.ts.net"
STATUS = {"BackendState": "Running", "Self": {"DNSName": HOST + "."}}


def _done(stdout="", code=0):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr="")


def fake_tailscale(web=None, frozen=False):
    """Tailscale falso CON estado: `web` = {puerto HTTPS: puerto local}.
    Por defecto Oraqlo (8765) ya está en su puerto propio. `frozen` simula que
    Tailscale no acepta cambios."""
    web = {HTTPS_PORT: 8765} if web is None else dict(web)
    calls: list[list[str]] = []

    def config():
        return {
            "TCP": {str(p): {"HTTPS": True} for p in web},
            "Web": {
                f"{HOST}:{p}": {"Handlers": {"/": {"Proxy": f"http://127.0.0.1:{local}"}}}
                for p, local in web.items()
            },
        }

    def run(cmd):
        args = cmd[1:]
        calls.append(args)
        if args[:2] == ["serve", "status"]:
            return _done(json.dumps(config()))
        if args[0] == "status":
            return _done(json.dumps(STATUS))
        if args[0] == "serve" and not frozen:
            https = next(int(a.split("=")[1]) for a in args if a.startswith("--https="))
            if args[-1] == "off":
                web.pop(https, None)
            else:
                web[https] = int(args[-1].rsplit(":", 1)[1])
        return _done()

    ts = TailscaleServe(exe="tailscale", run=run)
    ts.web = web  # type: ignore[attr-defined]  (para inspeccionarlo en los tests)
    return ts, calls


def test_url_y_puerto_publicado_en_su_puerto_propio():
    ts, _ = fake_tailscale()
    assert ts.url() == f"https://{HOST}:8443"
    assert ts.url(https=443) == f"https://{HOST}"
    assert ts.serving_port() == 8765
    assert ts.serving_port(443) is None


def test_ensure_publica_en_8443_y_solo_si_cambia():
    ts, calls = fake_tailscale()
    assert ts.ensure(8765) is False
    assert ts.ensure(8770) is True
    assert calls[-1] == ["serve", "--bg", "--https=8443", "http://127.0.0.1:8770"]
    assert ts.web == {8443: 8770}


def test_nunca_toca_la_raiz_de_otra_app():
    ts, _ = fake_tailscale(web={443: 8765})  # Mamoney en la raíz
    assert ts.free_https_port(8766) == 8443
    ts.ensure(8766, 8443)
    assert ts.web == {443: 8765, 8443: 8766}


def test_si_8443_es_de_otra_app_usa_el_siguiente():
    ts, _ = fake_tailscale(web={8443: 9000})
    assert ts.free_https_port(8766) == 8444
    ts2, _ = fake_tailscale(web={8443: 8766})  # ya es nuestro
    assert ts2.free_https_port(8766) == 8443


def test_migracion_deja_la_raiz_y_se_va_a_su_puerto(tmp_path):
    ts, calls = fake_tailscale(web={443: 8766})  # versión vieja: Oraqlo en la raíz
    set_published_https(tmp_path, None)
    (tmp_path / "tailscale.txt").write_text("on", encoding="utf-8")  # marca antigua
    assert published_https(tmp_path) == 443
    assert ts.migrate_from_root(tmp_path, 8766) == 8443
    assert ts.web == {8443: 8766}
    assert ["serve", "--https=443", "off"] in calls
    assert published_https(tmp_path) == 8443
    assert ts.migrate_from_root(tmp_path, 8766) is None  # ya migrado


def test_migracion_no_quita_la_raiz_si_ya_es_de_otra_app(tmp_path):
    ts, calls = fake_tailscale(web={443: 8765})  # Mamoney recuperó la raíz
    (tmp_path / "tailscale.txt").write_text("on", encoding="utf-8")
    assert ts.migrate_from_root(tmp_path, 8766) == 8443
    assert ts.web == {443: 8765, 8443: 8766}
    assert ["serve", "--https=443", "off"] not in calls


def test_disable_retira_solo_ese_puerto():
    ts, _ = fake_tailscale(web={443: 8765, 8443: 8766})
    assert ts.disable(8443)
    assert ts.web == {443: 8765}


def test_sin_tailscale_no_hace_nada():
    ts = TailscaleServe(exe="", run=lambda cmd: _done())
    assert not ts.installed and ts.url() is None and ts.ensure(8770) is False


def test_serve_vacio_o_json_roto():
    ts, _ = fake_tailscale(web={})
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
    assert tailscale_enabled(tmp_path) and published_https(tmp_path) == 443
    set_published_https(tmp_path, 8443)
    assert published_https(tmp_path) == 8443
    set_published_https(tmp_path, None)
    assert not tailscale_enabled(tmp_path)
