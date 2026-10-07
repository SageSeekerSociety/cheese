"""A task is the project's: everyone in it sees it. A private chat is two
people's, so nothing said there becomes a task — not by creating one there,
not by turning a message into one, and not by 芝士 proposing one."""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import (
    post_project,
    room_agent_seat,
    session_auth_headers,
)


def _private_chat(client):
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    r = client.get(
        f"/projects/{project['id']}/private-chat",
        params={"user_handle": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return project["id"], r.json()["data"]["id"]


def _tasks(client, chat):
    return client.get(
        f"/topics/{chat}/tasks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]


def test_a_task_is_not_created_in_a_private_chat(client):
    _, chat = _private_chat(client)

    r = client.post(
        f"/topics/{chat}/tasks",
        json={"title": "整理周报"},
        headers=session_auth_headers("alice"),
    )

    assert 400 <= r.status_code < 500
    assert _tasks(client, chat) == []


def test_a_message_in_a_private_chat_does_not_become_a_task(client):
    _, chat = _private_chat(client)
    said = client.post(
        f"/topics/{chat}/messages",
        json={"request_id": str(uuid.uuid4()), "content": "帮我整理周报"},
        headers=session_auth_headers("alice"),
    ).json()["data"]

    r = client.post(
        f"/blocks/{said['id']}/upgrade", headers=session_auth_headers("alice")
    )

    assert 400 <= r.status_code < 500
    assert _tasks(client, chat) == []


def test_an_ai_teammate_creates_no_task_in_a_private_chat(client):
    project, chat = _private_chat(client)
    token = mint_scoped_token(
        project_id=project, topic_id=chat, agent_handle=room_agent_seat(client, chat)
    )

    r = client.post(
        f"/topics/{chat}/teammate-tasks",
        json={"title": "整理周报", "summary": "每周五汇总", "start": True},
        headers={"X-Cheese-Token": token},
    )

    assert 400 <= r.status_code < 500
    assert (
        client.get(
            f"/topics/{chat}/tasks", headers=session_auth_headers("alice")
        ).json()["data"]["data"]
        == []
    )
