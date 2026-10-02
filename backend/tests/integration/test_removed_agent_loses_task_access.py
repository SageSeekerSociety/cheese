"""An agent taken out of a room loses its tasks there, credential or not.

A scoped credential is a signature: it proves which agent it was minted for and
until when, not that the agent still sits where it was minted. Every other room
route asks the roster; these are the ones that hand out a task's code or the
project's forge credential, so they must ask it too.
"""

import pytest

from app.domain.agent.harness.channel import mint_session_token
from tests.delivery import delivery_task
from tests.integration.conftest import (
    post_project,
    room_agent_seat,
    session_auth_headers,
)


@pytest.fixture
def seated(client):
    project = post_project(client, json={"name": "P"}, owner="alice")
    pid = project.json()["data"]["id"]
    tid = client.post(
        "/topics",
        json={"project_id": pid, "title": "T"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    seat = room_agent_seat(client, tid)
    task = delivery_task(client, tid, commit=False)
    headers = {"X-Cheese-Token": mint_session_token(pid, tid, seat)}
    return pid, tid, seat, task, headers


def _remove(client, tid, seat):
    r = client.delete(
        f"/topics/{tid}/members/{seat}", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text


def test_a_seated_agent_reaches_its_task(client, seated):
    pid, _, _, task, headers = seated
    route = f"/projects/{pid}/git/tasks/{task.id}"
    assert client.get(route, headers=headers).status_code == 200
    # No backup yet is an answer about the task, not about the caller.
    assert client.get(f"{route}/snapshots/latest", headers=headers).status_code == 404


def test_a_removed_agent_cannot_read_or_write_its_old_tasks(client, seated):
    pid, tid, seat, task, headers = seated
    _remove(client, tid, seat)
    route = f"/projects/{pid}/git/tasks/{task.id}"

    assert client.get(route, headers=headers).status_code == 403
    assert client.post(route, headers=headers).status_code == 403
    assert client.get(f"{route}/snapshots/latest", headers=headers).status_code == 403
    assert (
        client.get(
            f"{route}/snapshots/00000000-0000-0000-0000-000000000000", headers=headers
        ).status_code
        == 403
    )
    saved = client.put(
        f"{route}/snapshots/{'a' * 40}",
        content=b"bundle",
        headers={**headers, "X-Cheese-Head": "b" * 40, "X-Content-Sha256": "c" * 64},
    )
    assert saved.status_code == 403


def test_a_seated_agent_is_not_refused_the_forge_credential(client, seated):
    _, _, _, _, headers = seated
    # No forge is bound in this project, so the answer is about the project,
    # not a refusal of the caller.
    assert client.get("/sandbox/forge-token", headers=headers).status_code not in (
        401,
        403,
    )


def test_a_removed_agent_gets_no_forge_credential(client, seated):
    _, tid, seat, _, headers = seated
    _remove(client, tid, seat)
    assert client.get("/sandbox/forge-token", headers=headers).status_code == 403
