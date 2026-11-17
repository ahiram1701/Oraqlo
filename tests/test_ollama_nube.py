"""Ollama en la nube: nombres de modelo, catálogo, prueba de verdad y la cuenta."""

from __future__ import annotations

import io
import json

from oraqlo.llm import ollama
from oraqlo.llm.base import ChatMessage, LLMProvider, LLMResponse
from oraqlo.llm.ollama import (
    OllamaBackendConfig,
    OllamaProvider,
    api_cloud_name,
    is_cloud_model,
    local_cloud_name,
)
from oraqlo.services.doctor import explain_model_error, probe_model
from test_api import env  # noqa: F401  (fixture compartida)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture(monkeypatch) -> list[dict]:
    payloads: list[dict] = []

    def fake_urlopen(request, timeout):
        payloads.append(json.loads(request.data))
        return _Resp(json.dumps({"message": {"content": "ok"}, "model": "m"}).encode())

    monkeypatch.setattr(ollama.urllib.request, "urlopen", fake_urlopen)
    return payloads


def test_nombres_de_modelos_en_la_nube():
    assert is_cloud_model("deepseek-v4.1-flash:cloud") and is_cloud_model("gpt-oss:120b-cloud")
    assert not is_cloud_model("llama3.1")
    assert api_cloud_name("gpt-oss:120b-cloud") == "gpt-oss:120b"
    assert api_cloud_name("deepseek-v4.1-flash:cloud") == "deepseek-v4.1-flash"
    assert local_cloud_name("gpt-oss:120b") == "gpt-oss:120b-cloud"
    assert local_cloud_name("deepseek-v4.1-flash") == "deepseek-v4.1-flash:cloud"


def test_contra_ollama_com_el_modelo_va_sin_sufijo(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "k")
    payloads = _capture(monkeypatch)
    OllamaProvider(OllamaBackendConfig.cloud()).chat(
        [ChatMessage("user", "hola")], "gpt-oss:120b-cloud"
    )
    assert payloads[0]["model"] == "gpt-oss:120b"
    # En el Ollama del PC el sufijo se mantiene: es como él lo conoce.
    OllamaProvider().chat([ChatMessage("user", "hola")], "deepseek-v4.1-flash:cloud")
    assert payloads[1]["model"] == "deepseek-v4.1-flash:cloud"


def test_esfuerzo_de_razonamiento_como_think(monkeypatch):
    payloads = _capture(monkeypatch)
    OllamaProvider(effort="high").chat([ChatMessage("user", "x")], "gpt-oss:120b-cloud")
    OllamaProvider(effort="max").chat([ChatMessage("user", "x")], "gpt-oss:20b")
    OllamaProvider(effort="low").chat([ChatMessage("user", "x")], "deepseek-v4.1-flash:cloud")
    OllamaProvider().chat([ChatMessage("user", "x")], "llama3.1")
    assert [p.get("think") for p in payloads] == ["high", "high", True, None]


def test_catalogo_suma_los_de_la_nube_con_el_nombre_del_pc(monkeypatch):
    provider = OllamaProvider()
    monkeypatch.setattr(
        provider,
        "_get_json",
        lambda path, timeout_s=5.0: {"models": [{"name": "llama3.1", "size": 4}]},
    )
    monkeypatch.setattr(
        ollama,
        "fetch_cloud_catalog",
        lambda timeout_s=8.0: [{"name": "gpt-oss:120b", "size": 9}, {"name": "glm-5.3"}],
    )
    cat = {m["name"]: m for m in provider.catalog()}
    assert cat["llama3.1"] == {"name": "llama3.1", "cloud": False, "size": 4, "installed": True}
    assert cat["gpt-oss:120b-cloud"]["cloud"] and not cat["gpt-oss:120b-cloud"]["installed"]
    assert "glm-5.3:cloud" in cat


class _Fake(LLMProvider):
    def __init__(self, error: Exception | None = None, models=()):
        self.error = error
        self.models = list(models)

    def chat(self, messages, model, temperature=0.7, json_mode=False, **kwargs):
        if self.error:
            raise self.error
        return LLMResponse(content="ok", model=model)

    def list_models(self):
        return self.models


def test_probar_un_modelo_hace_una_llamada_de_verdad(env, monkeypatch):
    client, service, *_ = env
    monkeypatch.setattr(service.registry, "get", lambda name, effort=None: _Fake())
    ok = client.post(
        "/api/providers/ollama-local/test", json={"model": "deepseek-v4.1-flash:cloud"}
    ).json()
    assert ok["ok"] and ok["detail"].startswith("Responde")

    sign_in = _Fake(
        RuntimeError("Ollama rechazó la petición (HTTP 401). Inicia sesión en Ollama: abre la app…")
    )
    r = probe_model(
        type("R", (), {"config": service.config, "get": lambda self, n, e=None: sign_in})(),
        "ollama-local",
        "x:cloud",
    )
    assert not r["ok"] and "inicies sesión" in r["detail"]
    assert "no existe" in explain_model_error("Error HTTP 404 de http://localhost: model not found")


def test_un_modelo_cloud_no_hace_falta_descargarlo(env, monkeypatch):
    _client, service, *_ = env
    from oraqlo.config import RoleSettings

    service.config.roles["default"] = RoleSettings(
        provider="ollama-local", model="deepseek-v4.1-flash:cloud"
    )
    monkeypatch.setattr(service.registry, "get", lambda name, effort=None: _Fake(models=[]))
    assert service.needs_setup() is False  # antes mandaba a Bienvenida en cada arranque


def test_estado_de_la_cuenta_de_ollama(env, monkeypatch):
    client, service, *_ = env
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-key-123456")
    monkeypatch.setattr(
        service.registry, "get", lambda name, effort=None: _Fake(models=["llama3.1"])
    )
    body = client.get("/api/ollama").json()
    assert body["local"] is True and body["key_present"] is True and body["web_search"] is True
    assert "123456" not in json.dumps(body)  # la clave nunca sale entera
