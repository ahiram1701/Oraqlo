"""Todas las claves y APIs se configuran desde Ajustes (nada de editar archivos)."""

from __future__ import annotations

import os

from oraqlo.forecaster.markets import METACULUS_TOKEN_ENV, MarketError, MetaculusSource
from test_api import env  # noqa: F401  (fixture compartida)


def test_token_de_metaculus_desde_ajustes(env, monkeypatch):  # noqa: F811
    client, service, *_ = env
    monkeypatch.delenv(METACULUS_TOKEN_ENV, raising=False)
    assert client.get("/api/options").json()["metaculus"] is False
    assert (
        client.put(f"/api/keys/{METACULUS_TOKEN_ENV}", json={"value": "tok-123456789"}).status_code
        == 200
    )
    options = client.get("/api/options").json()
    assert options["metaculus"] is True and options["metaculus_hint"] == "tok-…6789"
    assert os.environ[METACULUS_TOKEN_ENV] == "tok-123456789"
    # Queda guardada sin que el usuario toque ningún archivo, y se puede quitar.
    assert "tok-123456789" in (service.config.home / ".env").read_text(encoding="utf-8")
    assert client.delete(f"/api/keys/{METACULUS_TOKEN_ENV}").status_code in (200, 204)
    assert client.get("/api/options").json()["metaculus"] is False


def test_probar_el_token_de_metaculus():
    assert MetaculusSource(token="").check() == (False, "No hay token de Metaculus.")

    def rejected(url, headers=None):
        raise MarketError("HTTP 401 en www.metaculus.com")

    ok, detail = MetaculusSource(token="malo", fetch=rejected).check()
    assert not ok and "no acepta este token" in detail
    ok, _ = MetaculusSource(token="bueno", fetch=lambda url, headers=None: {"results": []}).check()
    assert ok


def test_crear_usar_y_quitar_una_api_propia(env, monkeypatch):  # noqa: F811
    client, service, *_ = env
    monkeypatch.setattr(
        "oraqlo.services.doctor.probe_provider",
        lambda registry, name: type(
            "C", (), {"as_dict": lambda self: {"name": name, "ok": True}}
        )(),
    )
    body = {"type": "openai", "base_url": "http://127.0.0.1:9999/v1", "key": "sk-mia-123456"}
    out = client.put("/api/providers/custom/mi-servidor", json=body).json()
    assert out["check"]["ok"] is True
    [api] = out["providers"]
    assert api == {
        "name": "mi-servidor",
        "type": "openai",
        "base_url": "http://127.0.0.1:9999/v1",
        "key_present": True,
        "key_hint": "sk-m…3456",
        "used_by": [],
    }
    # Ya es un proveedor más: aparece en la lista y se puede elegir en un rol.
    assert "mi-servidor" in service.registry.names()
    assert os.environ["ORAQLO_KEY_MI_SERVIDOR"] == "sk-mia-123456"

    # Cambiar la URL sin tocar la clave la conserva.
    body2 = {"type": "openai", "base_url": "http://127.0.0.1:9000/v1"}
    [api] = client.put("/api/providers/custom/mi-servidor", json=body2).json()["providers"]
    assert api["base_url"].endswith(":9000/v1") and api["key_present"] is True

    # Si un rol la usa, no se puede quitar hasta cambiarlo.
    client.put("/api/config", json={"roles": {"juez": {"provider": "mi-servidor", "model": "x"}}})
    response = client.delete("/api/providers/custom/mi-servidor")
    assert response.status_code == 409 and "juez" in response.json()["detail"]
    client.put("/api/config", json={"roles": {"juez": {"provider": None, "model": None}}})

    assert client.delete("/api/providers/custom/mi-servidor").json() == []
    assert "mi-servidor" not in service.registry.names()
    assert "ORAQLO_KEY_MI_SERVIDOR" not in os.environ
    assert client.delete("/api/providers/custom/mi-servidor").status_code == 404


def test_nombres_y_urls_de_apis_propias(env):  # noqa: F811
    client, *_ = env
    bad_name = client.put("/api/providers/custom/Mi_Servidor", json={"base_url": "http://x"})
    assert bad_name.status_code == 400
    builtin = client.put("/api/providers/custom/openai", json={"base_url": "http://x"})
    assert builtin.status_code == 400 and "ya es un proveedor" in builtin.json()["detail"]
    no_url = client.put(
        "/api/providers/custom/local", json={"type": "ollama", "base_url": "localhost"}
    )
    assert no_url.status_code == 400 and "http" in no_url.json()["detail"]


def test_api_propia_sin_clave_no_la_pide(env):  # noqa: F811
    client, service, *_ = env
    client.put(
        "/api/providers/custom/lmlocal",
        json={"type": "openai", "base_url": "http://127.0.0.1:1/v1", "key": ""},
    )
    [api] = client.get("/api/providers/custom").json()
    assert api["key_present"] is False
    from oraqlo.llm.registry import provider_info

    assert provider_info("lmlocal", service.config).key_present is None  # no necesita clave


def test_los_mensajes_mandan_a_ajustes_no_a_variables(monkeypatch):
    import pytest

    from oraqlo.llm.base import ProviderUnavailableError
    from oraqlo.llm.openai_compat import OpenAICompatProvider

    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(ProviderUnavailableError) as err:
        OpenAICompatProvider(preset="mistral").chat([], model="m")
    assert "Ajustes" in str(err.value) and "variable" not in str(err.value)
