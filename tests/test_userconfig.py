"""Configuración desde la app: claves (.env), oraqlo.toml, diagnóstico y rutas de la API."""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from oraqlo.config import load_config
from oraqlo.services.userconfig import ConfigError, apply_changes, key_hint, set_secret
from test_api import env  # noqa: F401  (fixture compartida)

REMOTE = {"X-Forwarded-For": "100.64.0.2", "Tailscale-User-Login": "yo@github"}


@pytest.fixture
def clean_env(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setenv(var, "")
        monkeypatch.delenv(var)


def test_key_hint_no_revela_la_clave():
    assert key_hint("sk-ant-api03-abcdefghijklmnop") == "sk-a…mnop"
    assert key_hint("abc") == "…bc"
    assert key_hint(None) is None


def test_guardar_y_quitar_clave(tmp_path, clean_env):
    set_secret(tmp_path, "OPENAI_API_KEY", " sk-123456789 ", {"OPENAI_API_KEY"})
    assert os.environ["OPENAI_API_KEY"] == "sk-123456789"
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert 'OPENAI_API_KEY="sk-123456789"' in text
    set_secret(tmp_path, "OPENAI_API_KEY", None, {"OPENAI_API_KEY"})
    assert "OPENAI_API_KEY" not in os.environ
    assert "OPENAI_API_KEY" not in (tmp_path / ".env").read_text(encoding="utf-8")


def test_no_se_escriben_variables_arbitrarias(tmp_path, clean_env):
    with pytest.raises(ConfigError):
        set_secret(tmp_path, "PATH", "x", {"OPENAI_API_KEY"})
    with pytest.raises(ConfigError):
        set_secret(tmp_path, "OPENAI_API_KEY", "a\nB=c", {"OPENAI_API_KEY"})
    assert not (tmp_path / ".env").exists()


def test_editar_toml_conserva_comentarios(tmp_path):
    (tmp_path / "oraqlo.toml").write_text(
        '# mis notas\n[roles.juez]\nmodel = "x"  # el juez\n', encoding="utf-8"
    )
    config = apply_changes(
        tmp_path,
        {
            "roles": {"default": {"provider": "openai"}, "juez": {"model": None}},
            "updates_mode": "notify",
            "allowed_users": ["yo@github", " "],
        },
    )
    text = (tmp_path / "oraqlo.toml").read_text(encoding="utf-8")
    assert "# mis notas" in text
    assert config.role("juez").provider == "openai"
    assert config.updates.mode == "notify"
    assert config.server.allowed_users == ["yo@github"]
    assert load_config(home=tmp_path).updates.mode == "notify"


def test_cambio_invalido_no_toca_el_archivo(tmp_path):
    (tmp_path / "oraqlo.toml").write_text('[updates]\nmode = "auto"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        apply_changes(tmp_path, {"roles": {"juez": {"effort": "muchisimo"}}})
    with pytest.raises(ConfigError):
        apply_changes(tmp_path, {"roles": {"cocinero": {"model": "x"}}})
    assert (tmp_path / "oraqlo.toml").read_text(encoding="utf-8") == '[updates]\nmode = "auto"\n'


# ── API ──────────────────────────────────────────────────────────────────


def test_bienvenida_y_clave_desde_la_app(env):
    client, service, _app, _fake = env
    # Sin claves y con Ollama "disponible" (el fake) no hace falta configurar nada;
    # forzamos el caso real: Ollama local no responde.
    from oraqlo.llm.base import ProviderUnavailableError

    class Down:
        def is_available(self):
            return False

        def list_models(self):
            raise ProviderUnavailableError("sin ollama")

    service.registry.get = lambda name, effort=None: Down()  # type: ignore[method-assign]
    setup = client.get("/api/setup").json()
    assert setup == {
        "ready": False,
        "needs_setup": True,
        "local": True,
        "legacy_imported": False,
    }

    r = client.put("/api/keys/ANTHROPIC_API_KEY", json={"value": "sk-ant-xyz-123456"})
    assert r.status_code == 200
    anthropic = next(p for p in r.json()["providers"] if p["name"] == "anthropic")
    assert anthropic["key_present"] is True and anthropic["key_hint"] == "sk-a…3456"
    assert "sk-ant-xyz-123456" not in json.dumps(r.json())
    assert client.get("/api/setup").json()["ready"] is True
    assert service.config.role("juez").provider == "anthropic"

    assert client.delete("/api/keys/ANTHROPIC_API_KEY").status_code == 200
    assert client.put("/api/keys/HOME", json={"value": "x"}).status_code == 400


def test_saltar_bienvenida(env):
    client, service, *_ = env
    service.needs_setup = lambda: True  # type: ignore[method-assign]
    assert client.get("/api/setup").json()["needs_setup"] is True
    client.post("/api/setup/skip")
    assert client.get("/api/setup").json()["needs_setup"] is False


def test_ajustes_del_sistema_solo_desde_el_pc(env):
    client, *_ = env
    for method, url, body in [
        ("put", "/api/keys/OPENAI_API_KEY", {"value": "sk-x"}),
        ("put", "/api/config", {"updates_mode": "off"}),
        ("post", "/api/import-legacy", {"files": {}}),
        ("put", "/api/server/tailscale", {"enabled": True}),
    ]:
        r = client.request(method, url, json=body, headers=REMOTE)
        assert r.status_code == 403, url
        assert "PC" in r.json()["detail"]
    assert client.get("/api/setup", headers=REMOTE).json()["local"] is False


def test_config_por_api_recarga_sin_reiniciar(env):
    client, service, *_ = env
    r = client.put(
        "/api/config",
        json={
            "roles": {"default": {"provider": "openai", "model": "gpt-5"}},
            "updates_mode": "notify",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["roles"] == {"default": {"provider": "openai", "model": "gpt-5"}}
    assert service.config.role("estratega").model == "gpt-5"
    assert client.get("/api/health").json()["update"]["mode"] == "notify"
    bad = client.put("/api/config", json={"roles": {"juez": {"provider": "inventado"}}})
    assert bad.status_code == 400


def test_diagnostico_y_prueba_de_proveedor(env):
    client, *_ = env
    doctor = client.get("/api/doctor").json()
    assert doctor["ok"] is True
    assert {c["name"] for c in doctor["checks"]} >= {"Configuración", "Base de datos"}
    assert client.post("/api/providers/ollama-local/test").json()["ok"] is True
    assert client.post("/api/providers/nada/test").status_code == 404


def test_importar_historial_subiendo_archivos(env, tmp_path):
    from oraqlo.memory.store import JsonLedger
    from test_config_store import _forecast

    client, *_ = env
    legacy = tmp_path / "legacy" / "oraqlo_ledger.json"
    JsonLedger(legacy).record(_forecast("f1", source="llm"))
    text = legacy.read_text(encoding="utf-8")
    r = client.post("/api/import-legacy", json={"files": {"oraqlo_ledger.json": text}})
    assert r.status_code == 200, r.text
    assert r.json()["open_forecasts"] == 1
    assert client.get("/api/setup").json()["legacy_imported"] is True
    bad = client.post("/api/import-legacy", json={"files": {"otro.json": "{}"}})
    assert bad.status_code == 400


def test_publicar_en_tailscale_desde_la_app(tmp_path, monkeypatch):
    from test_api import _managed_app
    from test_network import fake_tailscale

    ts, calls = fake_tailscale(serve={})  # Tailscale corriendo, nada publicado
    app = _managed_app(tmp_path, monkeypatch, ts)
    with TestClient(app) as client:
        on = client.put("/api/server/tailscale", json={"enabled": True})
        # El fake no cambia su "serve status" tras publicar: la API lo detecta.
        assert on.status_code == 502
        assert ["serve", "--bg", "8765"] in calls

    ts, calls = fake_tailscale()  # ya publica 8765
    app = _managed_app(tmp_path, monkeypatch, ts)
    with TestClient(app) as client:
        on = client.put("/api/server/tailscale", json={"enabled": True}).json()
        assert on["enabled"] is True and on["url"] == "https://pc.tail1.ts.net"
        off = client.put("/api/server/tailscale", json={"enabled": False}).json()
        assert off["enabled"] is False
        assert ["serve", "--https=443", "off"] in calls


def test_volver_a_la_version_anterior(tmp_path, monkeypatch):
    from test_api import _managed_app
    from test_network import fake_tailscale

    ts, _ = fake_tailscale()
    app = _managed_app(tmp_path, monkeypatch, ts)
    monkeypatch.setattr(app.state.updater, "rollback", lambda: "0.2.2")
    with TestClient(app) as client:
        r = client.post("/api/update/rollback").json()
        assert r == {"version": "0.2.2", "restarting": True, "detail": "Volviendo a 0.2.2..."}
        assert app.state.restart_requested is True
