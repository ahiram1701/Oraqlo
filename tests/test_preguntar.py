"""Cuando internet no basta, Oraqlo pausa el análisis y le pregunta al usuario."""

from __future__ import annotations

import json
from datetime import timedelta

from oraqlo.llm.base import LLMResponse
from oraqlo.llm.websearch import SearchResult
from oraqlo.research import ClarifyQuestion, Fact, ResearchBrief, Researcher
from oraqlo.services.push import job_notification
from test_api import (
    RoleAwareFake,
    env,  # noqa: F401  (fixture compartida)
    sse,
    wait_job,
)

AHORROS = {
    "text": "¿Cuántos meses de gastos tienes ahorrados?",
    "why": "Decide cuánto aguantas sin ingresos.",
    "options": ["Menos de 3", "3 a 6", "Más de 6"],
}
SOCIO = {"text": "¿Emprenderías con un socio?", "why": "Reparte el riesgo."}


class AskingFake(RoleAwareFake):
    """Como RoleAwareFake, pero el investigador pregunta según un guion por ronda."""

    def __init__(self, rounds: list[list[dict]]) -> None:
        super().__init__()
        self.rounds = rounds
        self.clarify_inputs: list[str] = []
        self.oracle_inputs: list[str] = []

    def chat(self, messages, model, temperature=0.7, json_mode=False, **kwargs):
        system = messages[0].content
        if "SOLO el usuario" in system:
            self.clarify_inputs.append(messages[-1].content)
            n = len(self.clarify_inputs) - 1
            questions = self.rounds[n] if n < len(self.rounds) else []
            return LLMResponse(content=json.dumps({"questions": questions}), model=model)
        if '"outcomes"' in system:
            self.oracle_inputs.append(" ".join(m.content for m in messages[1:]))
        return super().chat(messages, model, temperature, json_mode, **kwargs)


def _use(monkeypatch, service, fake) -> None:
    monkeypatch.setattr(service.registry, "get", lambda name, effort=None: fake)


def _ask(client, mode: str = "forecast") -> dict:
    r = client.post("/api/ask", json={"question": "¿Dejo mi trabajo para emprender?", "mode": mode})
    assert r.status_code == 202
    return wait_job(client, r.json()["job_id"])


def test_pausa_pregunta_y_reanuda_con_las_respuestas(env, monkeypatch):  # noqa: F811
    client, service, _app, _ = env
    fake = AskingFake([[AHORROS], []])
    _use(monkeypatch, service, fake)

    paused = _ask(client)
    assert paused["status"] == "done" and paused["run_id"] is None
    result = paused["result"]
    assert result["kind"] == "questions" and result["mode"] == "forecast"
    assert [q["text"] for q in result["questions"]] == [AHORROS["text"]]
    assert result["questions"][0]["options"] == AHORROS["options"]
    events = sse(client.get(f"/api/jobs/{paused['id']}/events").text)
    assert any(e[1] == "questions" for e in events)
    assert ("phase", {"name": "preguntar"}) in [(e[1], e[2]) for e in events]
    assert not fake.oracle_inputs  # no estimó nada antes de preguntar

    title, body, url = job_notification(paused)  # type: ignore[misc]
    assert title == "Oraqlo necesita tu respuesta"
    assert AHORROS["text"] in body and url == f"/?job={paused['id']}"

    qid = result["questions"][0]["id"]
    r = client.post(f"/api/jobs/{paused['id']}/answer", json={"answers": {qid: "3 a 6"}})
    assert r.status_code == 202
    done = wait_job(client, r.json()["job_id"])
    assert done["status"] == "done" and done["result"]["kind"] == "forecast"
    assert done["result"]["clarifications"] == [{"question": AHORROS["text"], "answer": "3 a 6"}]
    assert "3 a 6" in fake.oracle_inputs[-1]  # la respuesta llega al oráculo
    assert "Lo que nos contaste" in done["result"]["markdown"]
    assert "Ya se le preguntó" in fake.clarify_inputs[-1]  # 2.ª ronda sabe qué preguntó

    # El job en pausa apunta al que siguió: responder otra vez no lanza otro análisis.
    again = client.post(f"/api/jobs/{paused['id']}/answer", json={"answers": {qid: "x"}})
    assert again.json()["job_id"] == r.json()["job_id"]


def test_varias_rondas_sin_repetir_preguntas(env, monkeypatch):  # noqa: F811
    client, service, _app, _ = env
    fake = AskingFake([[AHORROS], [AHORROS, SOCIO], []])
    _use(monkeypatch, service, fake)

    first = _ask(client)
    q1 = first["result"]["questions"][0]["id"]
    second_id = client.post(
        f"/api/jobs/{first['id']}/answer", json={"answers": {q1: "Más de 6"}}
    ).json()["job_id"]
    second = wait_job(client, second_id)
    # La repetida se descarta en código: solo queda la nueva.
    assert [q["text"] for q in second["result"]["questions"]] == [SOCIO["text"]]
    q2 = second["result"]["questions"][0]["id"]
    assert q2 != q1

    third_id = client.post(f"/api/jobs/{second['id']}/answer", json={"answers": {q2: "Sí"}}).json()[
        "job_id"
    ]
    third = wait_job(client, third_id)
    assert third["status"] == "done" and third["result"]["kind"] == "forecast"
    assert [c["answer"] for c in third["result"]["clarifications"]] == ["Más de 6", "Sí"]


def test_continuar_sin_responder_no_vuelve_a_preguntar(env, monkeypatch):  # noqa: F811
    client, service, _app, _ = env
    fake = AskingFake([[AHORROS], [SOCIO]])
    _use(monkeypatch, service, fake)

    paused = _ask(client)
    r = client.post(f"/api/jobs/{paused['id']}/answer", json={"answers": None})
    done = wait_job(client, r.json()["job_id"])
    assert done["result"]["kind"] == "forecast"
    assert len(fake.clarify_inputs) == 1  # omitir = no más preguntas
    assert done["result"]["clarifications"] == [{"question": AHORROS["text"], "answer": None}]
    assert "Sin responder" in done["result"]["markdown"]
    # Sin respuestas no hay datos: el oráculo se modera como siempre que no sabe nada.
    assert "world model vacío" in fake.oracle_inputs[-1]


def test_responder_un_job_que_no_espera_respuestas(env, monkeypatch):  # noqa: F811
    client, service, _app, _ = env
    _use(monkeypatch, service, AskingFake([]))
    done = _ask(client)
    assert done["result"]["kind"] == "forecast"  # nada que preguntar: flujo de siempre
    r = client.post(f"/api/jobs/{done['id']}/answer", json={"answers": {}})
    assert r.status_code == 409
    assert client.post("/api/jobs/nada/answer", json={"answers": None}).status_code == 404


def test_fuera_de_la_web_no_pregunta(env, monkeypatch):  # noqa: F811
    _client, service, _app, _ = env
    fake = AskingFake([[AHORROS]])
    _use(monkeypatch, service, fake)
    result = service.ask_forecast("¿Sube?", 30, research=False)
    assert result["kind"] == "forecast" and not fake.clarify_inputs


class _Web:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def is_available(self) -> bool:
        return True

    def search(self, query, max_results=5):
        self.queries.append(query)
        return [SearchResult("Nuevo", "https://n.example", "dato nuevo")]

    def fetch(self, url, max_chars=6000):
        return "contenido"


class _Scripted(RoleAwareFake):
    def __init__(self, replies: list[dict]) -> None:
        super().__init__()
        self.replies = replies
        self.inputs: list[str] = []

    def chat(self, messages, model, temperature=0.7, json_mode=False, **kwargs):
        self.inputs.append(messages[-1].content)
        return LLMResponse(content=json.dumps(self.replies.pop(0)), model=model)


def test_brief_checkpoint_y_reanudacion():
    brief = ResearchBrief(
        facts=[Fact("El mercado creció 12%.", "https://x.example")],
        queries=["mercado 2026"],
        sources=[SearchResult("Informe", "https://x.example", "creció 12%")],
        fetched={"https://x.example": "texto completo"},
        clarifications=[ClarifyQuestion("p1", "¿Presupuesto?", answer="5.000 €")],
    )
    restored = ResearchBrief.from_dict(json.loads(json.dumps(brief.to_dict())))
    assert restored == brief
    assert "DATOS QUE APORTÓ EL USUARIO" in restored.as_context()
    assert [o.entity_id for o in restored.as_observations()] == ["investigacion", "usuario"]

    # Reanudar: no vuelve a generar consultas iniciales; la reflexión ve la respuesta
    # y puede buscar algo más concreto.
    web = _Web()
    provider = _Scripted(
        [
            {"action": "search", "queries": ["coste local 5000 euros"]},
            {"action": "synthesize"},
            {"facts": [{"text": "Abrir cuesta 4.000 €.", "source": "https://n.example"}]},
        ]
    )
    resumed = Researcher(web, provider, "m").investigate(  # type: ignore[arg-type]
        "¿Abro un local?", timedelta(days=90), resume=restored
    )
    assert web.queries == ["coste local 5000 euros"]
    assert "5.000 €" in provider.inputs[0]
    assert resumed.queries == ["mercado 2026", "coste local 5000 euros"]
    assert resumed.clarifications == restored.clarifications
    assert resumed.fetched == {"https://x.example": "texto completo"}
