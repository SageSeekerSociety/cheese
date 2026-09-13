"""The actual HTTP identity and room policy protect every RC controller route."""

import time

import pytest

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import session_auth_headers


@pytest.mark.parametrize(
    "status,age,connected",
    [("archived", 0, False), ("active", 120, False), ("active", 0, True)],
)
def test_control_state_reads_tasks_only_for_connected_session(
    client, place, monkeypatch, status, age, connected
):
    from app.api.routes.remote_control import store
    from app.domain.agent.remote_control import key

    project, topic = place

    async def create_session():
        service = store()
        row = await service.create(
            {"p": project, "t": topic, "exp": int(time.time()) + 3600},
            {
                "execution": {
                    "resource_id": topic,
                    "execution": {"device_id": "old-device"},
                }
            },
        )
        await service.update(
            row["id"], {"status": status, "last_seen": time.time() - age}
        )
        return row["id"]

    async def remote_tasks(target, request):
        if not connected:
            raise RuntimeError("Archived executor is offline")
        return {"tasks": [{"task_id": "running-task"}]}

    monkeypatch.setattr(
        "app.api.routes.remote_control.private_chat.control", remote_tasks
    )
    sid = client.portal.call(create_session)
    try:
        response = client.get(
            f"/topics/{topic}/agent/control", headers=session_auth_headers("alice")
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["connected"] is connected
        assert data["tasks"] == (
            {"running-task": {"task_id": "running-task"}} if connected else {}
        )
    finally:

        async def cleanup():
            await store().redis.delete(key(sid), key(topic, "current"))

        client.portal.call(cleanup)


@pytest.fixture(autouse=True)
def signing_key(monkeypatch):
    monkeypatch.setattr("app.core.tokens._SECRET", "rc-http-test-key-at-least-32-bytes")


@pytest.fixture
def place(client):
    project = client.post(
        "/projects", json={"name": "RC test", "owner_handle": "alice"}
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "RC", "created_by": "alice"},
    ).json()["data"]
    return project["id"], topic["id"]


@pytest.mark.parametrize(
    "identity,expected", [("alice", 200), ("outsider", 403), (None, 401)]
)
def test_control_state_requires_room_membership_even_with_rollout_disabled(
    client, place, monkeypatch, identity, expected
):
    monkeypatch.setattr(settings, "authz_enforce_topic_access", False)
    _, topic = place
    response = client.get(
        f"/topics/{topic}/agent/control",
        headers=session_auth_headers(identity) if identity else {},
    )
    assert response.status_code == expected, response.text


@pytest.mark.parametrize(
    "endpoint,payload",
    [
        (
            "control",
            {
                "session_id": "s",
                "request": {
                    "subtype": "set_permission_mode",
                    "mode": "bypassPermissions",
                },
            },
        ),
        (
            "answer",
            {"session_id": "s", "request_id": "q", "response": {"behavior": "allow"}},
        ),
        ("message", {"session_id": "s", "content": "approve myself"}),
    ],
)
def test_agent_cannot_approve_or_control_itself(client, place, endpoint, payload):
    project, topic = place
    token = mint_scoped_token(project_id=project, topic_id=topic, remote_control=True)
    response = client.post(
        f"/topics/{topic}/agent/{endpoint}",
        json=payload,
        headers={"X-Cheese-Token": token},
    )
    assert response.status_code == 403, response.text


def test_bootstrap_rejects_a_place_from_another_project(client, place):
    _, topic = place
    other = client.post(
        "/projects", json={"name": "Other", "owner_handle": "bob"}
    ).json()["data"]
    token = mint_scoped_token(
        project_id=other["id"], topic_id=topic, remote_control=True
    )
    response = client.post(
        "/v1/code/sessions", json={}, headers={"X-Cheese-Token": token}
    )
    assert response.status_code == 403, response.text
