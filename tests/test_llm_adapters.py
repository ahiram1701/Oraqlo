"""Capa LLM: salida estructurada, reintentos y adaptadores (todo offline)."""

from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from oraqlo.llm.base import (
    ChatMessage,
    LLMError,
    LLMProvider,
    LLMResponse,
    ProviderUnavailableError,
)
from oraqlo.llm.retry import TransientError, retry_call
from oraqlo.llm.structured import JsonChatError, chat_json, chat_model, extract_json_object

# ── extracción y salida estructurada ──────────────────────────────────────


def test_extract_json_tolera_llaves_sueltas_y_fences():
    text = 'Uso {x} como marcador.\n```json\n{"a": 1, "b": {"c": [1, 2]}}\n```\nFin {y}'
    assert extract_json_object(text) == {"a": 1, "b": {"c": [1, 2]}}


def test_extract_json_sin_objeto_falla():
    with pytest.raises(ValueError):
        extract_json_object("nada por aquí")


class ScriptedProvider(LLMProvider):
    def __init__(self, responses: list[str], schema_support: bool = False) -> None:
        self.responses = responses
        self.supports_json_schema = schema_support
        self.kwargs: list[dict] = []

    def chat(self, messages, model, temperature=0.7, json_mode=False, **kwargs):
        self.kwargs.append(kwargs)
        return LLMResponse(content=self.responses[len(self.kwargs) - 1], model=model)


class Verdict(BaseModel):
    decision: str
    confidence: float


def test_chat_model_valida_y_reintenta_con_feedback():
    p = ScriptedProvider(['{"decision": "emitir"}', '{"decision": "emitir", "confidence": 0.8}'])
    v = chat_model(p, "m", "sys", "user", Verdict)
    assert v == Verdict(decision="emitir", confidence=0.8)
    assert len(p.kwargs) == 2


def test_chat_model_agota_reintentos():
    p = ScriptedProvider(["{}", "{}"])
    with pytest.raises(JsonChatError):
        chat_model(p, "m", "s", "u", Verdict, max_retries=1)


def test_esquema_nativo_solo_si_el_proveedor_lo_soporta():
    plain = ScriptedProvider(['{"decision": "x", "confidence": 1}'])
    chat_model(plain, "m", "s", "u", Verdict)
    assert "json_schema" not in plain.kwargs[0]

    native = ScriptedProvider(['{"decision": "x", "confidence": 1}'], schema_support=True)
    chat_model(native, "m", "s", "u", Verdict)
    assert native.kwargs[0]["json_schema"]["properties"]["decision"]["type"] == "string"


def test_chat_json_compat():
    assert chat_json(ScriptedProvider(['ok {"a": 1}']), "m", "s", "u") == {"a": 1}


# ── reintentos ────────────────────────────────────────────────────────────


def test_retry_reintenta_transitorios_y_relanza_la_causa():
    calls, sleeps = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise TransientError("503")
        return "ok"

    assert retry_call(flaky, attempts=3, sleep=sleeps.append) == "ok"
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0]

    def always():
        try:
            raise ProviderUnavailableError("caído")
        except ProviderUnavailableError as e:
            raise TransientError("x") from e

    with pytest.raises(ProviderUnavailableError, match="caído"):
        retry_call(always, attempts=2, sleep=lambda s: None)


# ── Ollama ────────────────────────────────────────────────────────────────


def test_ollama_envia_esquema_y_reintenta_503(monkeypatch):
    from oraqlo.llm import ollama

    payloads, state = [], {"n": 0}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request, timeout):
        payloads.append(json.loads(request.data))
        state["n"] += 1
        if state["n"] == 1:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", None, io.BytesIO(b"busy"))
        return Resp(json.dumps({"message": {"content": '{"a": 1}'}, "model": "m"}).encode())

    monkeypatch.setattr(ollama.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("oraqlo.llm.retry.time.sleep", lambda s: None)
    provider = ollama.OllamaProvider()
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    out = provider.chat([ChatMessage("user", "hola")], "m", json_mode=True, json_schema=schema)
    assert out.content == '{"a": 1}'
    assert state["n"] == 2
    assert payloads[0]["format"] == schema


def test_ollama_401_no_reintenta(monkeypatch):
    from oraqlo.llm import ollama

    calls = []

    def fake_urlopen(request, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(request.full_url, 401, "no", None, io.BytesIO(b""))

    monkeypatch.setattr(ollama.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ollama.OllamaUnavailableError, match="Inicia sesión en Ollama"):
        ollama.OllamaProvider().chat([ChatMessage("user", "x")], "m")
    assert len(calls) == 1


# ── Anthropic ─────────────────────────────────────────────────────────────


class FakeAnthropicMessages:
    def __init__(self, response):
        self.response = response
        self.params: dict = {}

    def create(self, **params):
        self.params = params
        return self.response


def _anthropic_response(text="hola", stop_reason="end_turn"):
    return SimpleNamespace(
        id="msg_1",
        model="claude-opus-5-5",
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category="cyber") if stop_reason == "refusal" else None,
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
    )


def _anthropic(response, **kw):
    from oraqlo.llm.claude import AnthropicProvider

    messages = FakeAnthropicMessages(response)
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages), messages=messages)
    return AnthropicProvider(client=client, **kw), messages


def test_anthropic_sin_temperature_con_effort_esquema_y_fallbacks():
    provider, api = _anthropic(_anthropic_response('{"a": 1}'), effort="high")
    schema = {"type": "object", "properties": {"a": {"type": "object", "properties": {}}}}
    out = provider.chat(
        [ChatMessage("system", "eres x"), ChatMessage("user", "hola")],
        "claude-opus-5-5",
        temperature=0.9,
        json_schema=schema,
    )
    assert out.content == '{"a": 1}'  # solo bloques de texto
    p = api.params
    assert "temperature" not in p
    assert p["system"] == "eres x" and p["messages"] == [{"role": "user", "content": "hola"}]
    assert p["output_config"]["effort"] == "high"
    fmt = p["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False
    assert fmt["schema"]["properties"]["a"]["additionalProperties"] is False
    assert p["fallbacks"] == "default" and p["betas"]


def test_anthropic_rechazo_es_error_claro():
    provider, _ = _anthropic(_anthropic_response(stop_reason="refusal"))
    with pytest.raises(LLMError, match="cyber"):
        provider.chat([ChatMessage("user", "x")], "claude-opus-5-5")


def test_anthropic_effort_invalido():
    from oraqlo.llm.claude import AnthropicProvider

    with pytest.raises(ValueError):
        AnthropicProvider(effort="altísimo")


# ── OpenAI-compatible ────────────────────────────────────────────────────


def _openai(preset):
    from oraqlo.llm.openai_compat import OpenAICompatProvider

    seen: dict = {}

    def create(**params):
        seen.update(params)
        return SimpleNamespace(
            id="c1",
            model=params["model"],
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return OpenAICompatProvider(preset=preset, client=client), seen


def test_openai_usa_json_schema_y_groq_json_object():
    schema = {"type": "object"}
    provider, seen = _openai("gemini")
    assert provider.supports_json_schema
    provider.chat([ChatMessage("user", "x")], "gemini-2.5-pro", json_mode=True, json_schema=schema)
    assert seen["response_format"]["type"] == "json_schema"
    assert seen["temperature"] == 0.7

    groq, seen2 = _openai("groq")
    assert not groq.supports_json_schema
    groq.chat([ChatMessage("user", "x")], "llama", json_mode=True)
    assert seen2["response_format"] == {"type": "json_object"}


def test_openai_sin_clave_avisa(monkeypatch):
    from oraqlo.llm.openai_compat import OpenAICompatProvider

    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(ProviderUnavailableError, match="MISTRAL_API_KEY"):
        OpenAICompatProvider(preset="mistral").chat([ChatMessage("user", "x")], "m")


def test_preset_desconocido():
    from oraqlo.llm.openai_compat import OpenAICompatProvider

    with pytest.raises(ValueError):
        OpenAICompatProvider(preset="inventado")


def test_ollama_timeout_y_500_local_no_se_reintentan(monkeypatch):
    from oraqlo.llm import ollama

    calls = []

    def slow(request, timeout):
        calls.append(1)
        raise TimeoutError("timed out")

    monkeypatch.setattr(ollama.urllib.request, "urlopen", slow)
    with pytest.raises(ollama.OllamaUnavailableError, match="tardó más de 300 s"):
        ollama.OllamaProvider().chat([ChatMessage("user", "x")], "m")
    assert len(calls) == 1  # antes: 3 intentos de 5 min cada uno

    calls.clear()

    def no_memory(request, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(request.full_url, 500, "x", None, io.BytesIO(b"out of memory"))

    monkeypatch.setattr(ollama.urllib.request, "urlopen", no_memory)
    with pytest.raises(ollama.OllamaUnavailableError, match="out of memory"):
        ollama.OllamaProvider().chat([ChatMessage("user", "x")], "m")
    assert len(calls) == 1


def test_openai_modelo_de_razonamiento_sin_temperature():
    import httpx
    import openai

    from oraqlo.llm.openai_compat import OpenAICompatProvider

    calls: list[dict] = []

    def create(**params):
        calls.append(dict(params))
        if "temperature" in params:
            request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
            raise openai.BadRequestError(
                "Unsupported value: 'temperature' does not support 0.2 with this model.",
                response=httpx.Response(400, request=request),
                body=None,
            )
        return SimpleNamespace(
            id="c1",
            model=params["model"],
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    provider = OpenAICompatProvider(preset="openai", client=client)
    assert provider.chat([ChatMessage("user", "x")], "gpt-5", temperature=0.2).content == "ok"
    assert "temperature" in calls[0] and "temperature" not in calls[1]
    calls.clear()
    provider.chat([ChatMessage("user", "x")], "gpt-5", temperature=0.2)
    assert len(calls) == 1 and "temperature" not in calls[0]  # lo recuerda por modelo
