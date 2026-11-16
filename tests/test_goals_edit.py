"""Editar objetivos e hitos desde la app (texto, fechas y estado)."""

from __future__ import annotations

from oraqlo.goaltrack import GoalTracker
from test_api import env  # noqa: F401  (fixture compartida)
from test_goaltrack import _plan


def _goal(service):
    return GoalTracker(service.store.goals, service.store.ledger).track(_plan()).id


def test_editar_objetivo_y_hito(env):
    client, service, *_ = env
    goal_id = _goal(service)

    r = client.patch(
        f"/api/goals/{goal_id}", json={"goal": "  Correr 10 km  ", "deadline": "2027-03-01"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["goal"] == "Correr 10 km" and r.json()["deadline"] == "2027-03-01"

    r = client.patch(
        f"/api/goals/{goal_id}/milestones/0",
        json={"text": "Primer 5 km", "target_date": "2027-01-15", "status": "cumplido"},
    )
    assert r.status_code == 200, r.text
    m = r.json()["milestones"][0]
    assert (m["text"], m["target_date"], m["status"]) == ("Primer 5 km", "2027-01-15", "cumplido")


def test_ediciones_invalidas_se_rechazan(env):
    client, service, *_ = env
    goal_id = _goal(service)
    assert client.patch(f"/api/goals/{goal_id}", json={"deadline": "mañana"}).status_code == 422
    assert (
        client.patch(f"/api/goals/{goal_id}/milestones/99", json={"text": "x"}).status_code == 422
    )
    assert (
        client.patch(f"/api/goals/{goal_id}/milestones/0", json={"status": "a medias"}).status_code
        == 422
    )
    assert client.patch(f"/api/goals/{goal_id}", json={"goal": ""}).status_code == 422
    assert client.patch("/api/goals/no-existe", json={"goal": "x"}).status_code == 404
