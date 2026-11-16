"""Auto-actualización: releases, verificación, activación, rollback y lanzador (offline)."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from oraqlo import updater as up
from oraqlo.launcher import RESTART_EXIT_CODE, Launcher
from oraqlo.updater import Layout, UpdateError, Updater

REPO = "dueño/oraqlo"


def _release(version: str, prerelease: bool = False, sums: bool = True) -> dict:
    assets = [
        {
            "name": f"oraqlo-{version}-py3-none-any.whl",
            "browser_download_url": f"https://x/{version}.whl",
        }
    ]
    if sums:
        assets.append({"name": "SHA256SUMS", "browser_download_url": f"https://x/{version}.sums"})
    return {
        "tag_name": f"v{version}",
        "assets": assets,
        "html_url": "https://x",
        "prerelease": prerelease,
    }


def _updater(tmp_path, releases: dict, monkeypatch, current="0.2.0") -> Updater:
    monkeypatch.setattr(up, "__version__", current)
    u = Updater(tmp_path, REPO)

    def fetch(url):
        if url.endswith("/releases/latest"):
            if "latest" not in releases:
                raise up.NoReleases("sin releases")
            return releases["latest"]
        return releases.get("list", [])

    u.fetch = fetch
    return u


# ── versiones y releases ─────────────────────────────────────────────────


def test_is_newer():
    assert up.is_newer("0.3.0", "0.2.9")
    assert not up.is_newer("0.2.0", "0.2.0")
    assert not up.is_newer("basura", "0.1.0")
    assert up.is_newer("0.2.0", "0.2.0.dev3+gabc")  # una dev no tapa la release


def test_check_estados(tmp_path, monkeypatch):
    assert _updater(tmp_path, {}, monkeypatch).check().status == "up-to-date"
    same = _updater(tmp_path, {"latest": _release("0.2.0")}, monkeypatch)
    assert same.check().status == "up-to-date"
    newer = _updater(tmp_path, {"latest": _release("0.3.0")}, monkeypatch)
    r = newer.check()
    assert (r.status, r.version) == ("available", "0.3.0")


def test_canal_beta_incluye_prereleases(tmp_path, monkeypatch):
    releases = {"list": [_release("0.3.0"), _release("0.4.0rc1", prerelease=True)]}
    u = _updater(tmp_path, releases, monkeypatch)
    u.channel = "beta"
    assert u.check().version == "0.4.0rc1"


def test_copia_no_gestionada_no_se_actualiza(tmp_path, monkeypatch):
    u = _updater(tmp_path, {"latest": _release("0.3.0")}, monkeypatch)
    r = u.stage(managed=False)
    assert r.status == "unmanaged" and "git" in r.detail


# ── verificación ─────────────────────────────────────────────────────────


def test_sha256(tmp_path):
    wheel = tmp_path / "oraqlo-0.3.0-py3-none-any.whl"
    wheel.write_bytes(b"contenido")
    good = hashlib.sha256(b"contenido").hexdigest()
    up.verify_sha256(wheel, f"{good}  {wheel.name}\n")
    with pytest.raises(UpdateError, match="no coincide"):
        up.verify_sha256(wheel, f"{'0' * 64}  {wheel.name}\n")
    with pytest.raises(UpdateError, match="no incluye"):
        up.verify_sha256(wheel, f"{good}  otro.whl\n")


def _stage_env(tmp_path, monkeypatch, wheel_bytes=b"rueda", sums_hash=None, attested=None):
    u = _updater(tmp_path, {"latest": _release("0.3.0")}, monkeypatch)
    digest = sums_hash or hashlib.sha256(wheel_bytes).hexdigest()

    def download(url, dest):
        dest.write_bytes(
            wheel_bytes
            if url.endswith(".whl")
            else f"{digest}  oraqlo-0.3.0-py3-none-any.whl".encode()
        )
        return dest

    u.download = download
    calls = []
    monkeypatch.setattr(up, "verify_attestation", lambda path, repo: attested)
    monkeypatch.setattr(
        up, "install_version", lambda layout, v, wheel, python=None: calls.append(("install", v))
    )
    monkeypatch.setattr(up, "preflight", lambda layout, v, db: calls.append(("preflight", v)))
    return u, calls


def test_stage_instala_y_prueba_sin_activar(tmp_path, monkeypatch):
    u, calls = _stage_env(tmp_path, monkeypatch)
    r = u.stage(managed=True)
    assert (r.status, r.version) == ("staged", "0.3.0")
    assert calls == [("install", "0.3.0"), ("preflight", "0.3.0")]
    assert u.layout.current is None  # preparar NO activa


def test_stage_aborta_con_hash_malo_o_attestation_invalida(tmp_path, monkeypatch):
    u, calls = _stage_env(tmp_path, monkeypatch, sums_hash="0" * 64)
    with pytest.raises(UpdateError, match="no coincide"):
        u.stage(managed=True)
    assert calls == []
    u2, calls2 = _stage_env(tmp_path / "b", monkeypatch, attested=False)
    with pytest.raises(UpdateError, match="attestation"):
        u2.stage(managed=True)
    assert calls2 == []


def test_release_sin_sums_se_rechaza(tmp_path, monkeypatch):
    u, _ = _stage_env(tmp_path, monkeypatch)
    u.fetch = lambda url: _release("0.3.0", sums=False)
    with pytest.raises(UpdateError, match="SHA256SUMS"):
        u.stage(managed=True)


# ── activación, poda y rollback ──────────────────────────────────────────


def _install_fake(layout: Layout, *versions: str) -> None:
    for v in versions:
        (layout.venv(v)).mkdir(parents=True)


def test_activar_hace_backup_y_cambia_puntero(tmp_path):
    from oraqlo.store import Store

    db = tmp_path / "data" / "oraqlo.db"
    Store(db).close()
    u = Updater(tmp_path, REPO, db_path=db)
    _install_fake(u.layout, "0.2.0", "0.3.0")
    u.layout.switch_to("0.2.0")
    backup = u.activate("0.3.0", tmp_path / "backups")
    assert backup.exists()
    assert (u.layout.current, u.layout.previous, u.layout.pending) == ("0.3.0", "0.2.0", "0.3.0")
    assert "activada 0.3.0" in (u.layout.root / "update.log").read_text(encoding="utf-8")


def test_prune_conserva_actual_y_anterior(tmp_path):
    layout = Layout(tmp_path)
    _install_fake(layout, "0.1.0", "0.2.0", "0.3.0", "0.10.0")
    layout.switch_to("0.3.0")
    layout.switch_to("0.10.0")
    assert sorted(layout.prune()) == ["0.1.0", "0.2.0"]
    assert layout.installed_versions() == ["0.3.0", "0.10.0"]  # orden por versión, no alfabético


def test_rollback_manual(tmp_path):
    u = Updater(tmp_path, REPO)
    with pytest.raises(UpdateError):
        u.rollback()
    _install_fake(u.layout, "0.2.0", "0.3.0")
    u.layout.switch_to("0.2.0")
    u.layout.switch_to("0.3.0")
    assert u.rollback() == "0.2.0"
    assert (u.layout.current, u.layout.previous, u.layout.pending) == ("0.2.0", "0.3.0", None)


def test_is_managed(tmp_path, monkeypatch):
    layout = Layout(tmp_path)
    monkeypatch.setattr(up.sys, "prefix", str(layout.versions / "0.3.0"))
    assert up.is_managed(layout)
    monkeypatch.setattr(up.sys, "prefix", str(tmp_path / "otro"))
    assert not up.is_managed(layout)


# ── lanzador ─────────────────────────────────────────────────────────────


class FakeProc:
    def __init__(self, code: int) -> None:
        self.code = code
        self.killed = False

    def poll(self):
        return None

    def wait(self):
        return -9 if self.killed else self.code

    def kill(self):
        self.killed = True


def _launcher(tmp_path, exit_codes: list[int], healthy_versions: set[str]):
    layout = Layout(tmp_path)
    layout.root.mkdir(parents=True)
    started: list[str] = []

    def spawn(cmd):
        version = Path(cmd[0]).parents[1].name
        started.append(version)
        return FakeProc(exit_codes.pop(0) if exit_codes else 0)

    def healthy(port):
        return started[-1] in healthy_versions

    launcher = Launcher(
        tmp_path,
        spawn=spawn,
        healthy=healthy,
        sleep=lambda s: None,
        health_timeout_s=3,
        busy=lambda port: False,
    )
    return launcher, layout, started


def test_lanzador_reinicia_tras_codigo_75_y_valida_la_nueva(tmp_path):
    launcher, layout, started = _launcher(tmp_path, [RESTART_EXIT_CODE, 0], {"0.2.0", "0.3.0"})
    layout.switch_to("0.2.0")
    layout._write("pending.txt", None)

    def spawn_then_update(cmd, _orig=launcher.spawn):
        proc = _orig(cmd)
        if len(started) == 1:
            layout.switch_to("0.3.0")  # el servidor activó la nueva antes de salir con 75
        return proc

    launcher.spawn = spawn_then_update
    assert launcher.run() == 0
    assert started == ["0.2.0", "0.3.0"]
    assert layout.pending is None  # la nueva respondió: deja de estar pendiente


def test_lanzador_revierte_version_rota(tmp_path):
    launcher, layout, started = _launcher(tmp_path, [0, 0], {"0.2.0"})
    layout.switch_to("0.2.0")
    layout.switch_to("0.3.0")  # rota: nunca responde a /api/health
    assert launcher.run() == 0
    assert started == ["0.3.0", "0.2.0"]
    assert (layout.current, layout.previous) == ("0.2.0", "0.3.0")
    assert (layout.root / "rolled_back.txt").exists()
    assert "ROLLBACK" in (layout.root / "update.log").read_text(encoding="utf-8")


def test_lanzador_se_detiene_tras_muchas_caidas(tmp_path):
    launcher, layout, started = _launcher(tmp_path, [1] * 10, {"0.2.0"})
    layout.switch_to("0.2.0")
    layout._write("pending.txt", None)
    assert launcher.run() == 1
    assert len(started) == 6


def test_lanzador_no_importa_oraqlo():
    """Debe funcionar aunque la versión instalada esté rota: solo stdlib."""
    source = Path(up.__file__).with_name("launcher.py").read_text(encoding="utf-8")
    assert "from oraqlo" not in source and "import oraqlo" not in source
    launcher_path = Path(up.__file__).with_name("launcher.py")
    result = subprocess.run(
        [sys.executable, "-I", str(launcher_path), "--help"], capture_output=True, text=True
    )
    assert "Lanzador de Oraqlo" in result.stdout


def test_punteros_con_bom_de_powershell(tmp_path):
    layout = Layout(tmp_path)
    layout.root.mkdir(parents=True)
    (layout.root / "current.txt").write_bytes("﻿0.3.0".encode())
    assert layout.current == "0.3.0"
    launcher = Launcher(tmp_path)
    assert launcher.read("current.txt") == "0.3.0"


@pytest.mark.parametrize("tag", ["v0.3.0", "oraqlo-v0.3.0", "0.3.0"])
def test_release_tag_formats(tag):
    data = _release("0.3.0") | {"tag_name": tag}
    info = up._release_from(data)
    assert info is not None and info.version == "0.3.0"


def test_release_tag_without_version_is_ignored():
    assert up._release_from(_release("0.3.0") | {"tag_name": "nightly"}) is None


def test_launcher_only_trusts_oraqlo_health():
    from oraqlo.launcher import is_oraqlo_health

    assert is_oraqlo_health(b'{"app": "oraqlo", "status": "ok", "version": "0.2.1"}')
    # Otra app en el mismo puerto con su propio /api/health no cuenta como sana.
    assert not is_oraqlo_health(b'{"status": "ok", "version": "0.7.0"}')
    assert not is_oraqlo_health(b"<html>")
    assert not is_oraqlo_health(b"[]")


def test_lanzador_salta_al_siguiente_puerto_si_otra_app_lo_ocupa(tmp_path):
    (tmp_path / "app").mkdir()
    taken = {8765, 8766}
    launcher = Launcher(tmp_path, port=8765, sleep=lambda s: None, busy=lambda p: p in taken)
    assert launcher.resolve_port() == 8767
    assert (tmp_path / "app" / "port.txt").read_text() == "8767"
    assert "8765 lo usa otra aplicación; uso 8767" in (tmp_path / "app" / "update.log").read_text(
        encoding="utf-8"
    )
    assert launcher.command("0.2.0")[-1] == "8767"


def test_lanzador_respeta_port_txt_y_espera_si_se_libera(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "port.txt").write_text("﻿9000", encoding="utf-8")
    checks: list[int] = []

    def busy(port):  # ocupado la primera vez (el proceso anterior aún lo suelta)
        checks.append(port)
        return len(checks) == 1

    launcher = Launcher(tmp_path, port=8765, sleep=lambda s: None, busy=busy)
    assert launcher.resolve_port() == 9000
    assert checks == [9000, 9000]


def test_abrir_lleva_al_navegador_si_ya_corre(tmp_path):
    from oraqlo.launcher import open_app

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "port.txt").write_text("8766", encoding="utf-8")
    launcher = Launcher(tmp_path, sleep=lambda s: None, healthy=lambda p: p == 8766)
    started, opened = [], []
    assert open_app(launcher, start=lambda: started.append(1), browse=opened.append) == 0
    assert started == [] and opened == ["http://127.0.0.1:8766/"]


def test_abrir_arranca_la_tarea_y_espera(tmp_path):
    from oraqlo.launcher import open_app

    (tmp_path / "app").mkdir()
    started: list[int] = []
    checks: list[int] = []

    def healthy(port):
        checks.append(port)
        return bool(started) and len(checks) > 3

    launcher = Launcher(tmp_path, sleep=lambda s: None, healthy=healthy)
    opened: list[str] = []
    assert open_app(launcher, start=lambda: started.append(1), browse=opened.append) == 0
    assert started == [1] and opened == ["http://127.0.0.1:8765/"]


def test_abrir_avisa_si_no_arranca(tmp_path):
    from oraqlo.launcher import open_app

    (tmp_path / "app").mkdir()
    launcher = Launcher(tmp_path, sleep=lambda s: None, healthy=lambda p: False)
    notes: list[str] = []
    code = open_app(
        launcher, start=lambda: None, browse=lambda u: None, notify=notes.append, timeout_s=3
    )
    assert code == 1 and "server.log" in notes[0]


def test_el_lanzador_se_actualiza_al_arrancar_la_version_nueva(tmp_path):
    layout = Layout(tmp_path)
    layout.root.mkdir(parents=True)
    (layout.root / "launcher.py").write_text("print('viejo')\n", encoding="utf-8")
    new = tmp_path / "pkg_launcher.py"
    new.write_text("print('nuevo')\n", encoding="utf-8")

    assert up.refresh_launcher(layout, new) is True
    assert (layout.root / "launcher.py").read_text(encoding="utf-8") == "print('nuevo')\n"
    assert (layout.root / "launcher.prev.py").read_text(encoding="utf-8") == "print('viejo')\n"
    assert "lanzador actualizado" in (layout.root / "update.log").read_text(encoding="utf-8")
    assert up.refresh_launcher(layout, new) is False  # ya igual: no toca nada


def test_lanzador_roto_no_reemplaza_al_bueno(tmp_path):
    layout = Layout(tmp_path)
    layout.root.mkdir(parents=True)
    (layout.root / "launcher.py").write_text("print('bueno')\n", encoding="utf-8")
    broken = tmp_path / "roto.py"
    broken.write_text("def (:\n", encoding="utf-8")
    assert up.refresh_launcher(layout, broken) is False
    assert (layout.root / "launcher.py").read_text(encoding="utf-8") == "print('bueno')\n"


def test_sin_instalacion_gestionada_no_hace_nada(tmp_path):
    from oraqlo import launcher

    assert up.refresh_launcher(Layout(tmp_path), Path(launcher.__file__)) is False
    assert not (tmp_path / "app" / "launcher.py").exists()


def test_instalacion_que_tarda_demasiado_es_un_error_visible(tmp_path, monkeypatch):
    layout = Layout(tmp_path)
    layout.root.mkdir(parents=True)
    wheel = tmp_path / "oraqlo-9.9.9-py3-none-any.whl"
    wheel.write_bytes(b"x")

    def slow(cmd, **kw):
        if "pip" in cmd:
            raise subprocess.TimeoutExpired(cmd, 900)
        Path(cmd[-1]).mkdir(parents=True)  # el venv sí se crea
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(up.subprocess, "run", slow)
    with pytest.raises(UpdateError, match="tardó más de 900 s"):
        up.install_version(layout, "9.9.9", wheel)
    assert not layout.venv("9.9.9").exists()  # nada a medias


def test_attestation_que_no_responde_cuenta_como_no_comprobable(monkeypatch):
    monkeypatch.setattr(up.shutil, "which", lambda name: "gh")

    def hang(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 120)

    monkeypatch.setattr(up.subprocess, "run", hang)
    assert up.verify_attestation(Path("x.whl"), "ahiram1701/Oraqlo") is None


@pytest.mark.skipif(sys.platform != "win32", reason="prioridades de Windows")
def test_el_servidor_sube_a_prioridad_normal():
    """Regresión 0.5.6: sin argtypes, SetPriorityClass fallaba en silencio."""
    code = (
        "import ctypes; from ctypes import wintypes\n"
        "k = ctypes.WinDLL('kernel32')\n"
        "k.GetCurrentProcess.restype = wintypes.HANDLE\n"
        "k.GetPriorityClass.argtypes = [wintypes.HANDLE]\n"
        "from oraqlo.cli import _normal_priority\n"
        "_normal_priority()\n"
        "print(hex(k.GetPriorityClass(k.GetCurrentProcess())))\n"
    )
    below_normal = 0x00004000  # como la tarea programada
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, creationflags=below_normal
    )
    assert out.stdout.strip() == "0x20", out.stderr  # NORMAL_PRIORITY_CLASS


def test_prune_reintenta_si_una_version_sigue_en_uso(tmp_path, monkeypatch):
    layout = Layout(tmp_path)
    _install_fake(layout, "0.1.0", "0.2.0", "0.3.0")
    layout.switch_to("0.2.0")
    layout.switch_to("0.3.0")
    real = shutil.rmtree

    def rmtree_bloqueado(path, onexc=None, **kw):
        # Como en Windows con la ventana vieja abierta: un archivo no se puede borrar.
        assert onexc is not None
        onexc(os.unlink, str(path) + "/pythonw.exe", PermissionError("en uso"))

    monkeypatch.setattr(shutil, "rmtree", rmtree_bloqueado)
    assert layout.prune() == []  # no la da por borrada
    assert "0.1.0" in layout.installed_versions()
    assert "se reintentará" in (layout.root / "update.log").read_text(encoding="utf-8")
    monkeypatch.setattr(shutil, "rmtree", real)
    assert layout.prune() == ["0.1.0"]
