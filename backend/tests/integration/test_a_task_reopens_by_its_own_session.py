"""A task's own session reopens it, as it may close it.

A closed task can still be talked to: its teammate answers when called. When
its owner asks it to carry on, it used to be refused (「只有任务负责人能重开」)
and could only ask the owner to press the button — after closing the same task
itself a moment earlier. The two that may close a task are the two that may
reopen it; a session working another task may do neither.
"""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import (
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
    task_agent_headers,
)


def _closed_task(client) -> tuple[dict, str]:
    data = post_project(client, {"name": "Reopen"}, owner="alice").json()["data"]
    room = data["root_topic_id"]
    task = open_task(client, room, "先做一半", owner="alice")
    closed = client.post(
        f"/topics/{task['id']}/close",
        json={"conclusion": "先到这里"},
        headers=session_auth_headers("alice"),
    )
    assert closed.status_code == 200, closed.text
    return task, room


def test_the_tasks_own_session_reopens_it(client):
    task, room = _closed_task(client)
    seat = room_agent_seat(client, room)

    reopened = client.post(
        f"/topics/{task['id']}/reopen",
        headers=task_agent_headers(task["project_id"], uuid.UUID(task["id"]), seat),
    )

    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["data"]["status"] == "open"


def test_another_tasks_session_does_not(client):
    task, room = _closed_task(client)
    other = open_task(client, room, "别的活", owner="alice")
    seat = room_agent_seat(client, room)

    refused = client.post(
        f"/topics/{task['id']}/reopen",
        headers=task_agent_headers(task["project_id"], uuid.UUID(other["id"]), seat),
    )

    assert refused.status_code in (403, 404), refused.text
    status = client.get(
        f"/topics/{task['id']}/task", headers=session_auth_headers("alice")
    ).json()["data"]["status"]
    assert status == "closed"


def test_the_rooms_session_does_not(client):
    """The room's session-wide credential reaches every task of its project, so
    it is the one that gets past the conversation check to the reopen rule."""
    task, room = _closed_task(client)
    token = mint_scoped_token(
        project_id=task["project_id"],
        topic_id=room,
        agent_handle=room_agent_seat(client, room),
        access_scope="project",
    )

    refused = client.post(
        f"/topics/{task['id']}/reopen", headers={"X-Cheese-Token": token}
    )

    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "taskOwnSessionReopens"
