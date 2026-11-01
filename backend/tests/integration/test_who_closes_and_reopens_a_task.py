"""Who may close a task, and who may reopen it.

Closing is not undone by reopening: it voids the task's open cards and closes
its pull request. So it stays with the people who work the task — its owner
and contributors — and with the task's own session. Reopening is undone by
closing again and is announced with who did it, so anyone in the project who
works tasks may do it, and so may the task's own session: a teammate that
closed its task too early takes that back. A mentor, who does not work tasks,
reopens nothing; a session working another task, or the room's session-wide
credential, does neither.
"""

import uuid

import pytest

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import (
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
    task_agent_headers,
)
from tests.integration.test_project_roles import _invite


def _task(client, *, closed: bool, contributors: tuple[str, ...] = ()):
    """A task alice owns in a project bob works in as a member and teacher
    joins as a mentor; ``contributors`` work it beside alice."""
    data = post_project(client, {"name": "Reopen"}, owner="alice").json()["data"]
    room = data["root_topic_id"]
    _invite(client, data["id"], "bob", role="member")
    _invite(client, data["id"], "teacher", role="mentor")
    task = open_task(client, room, "先做一半", owner="alice")
    if contributors:
        set_ = client.patch(
            f"/topics/{task['id']}/task",
            json={"contributor_handles": list(contributors)},
            headers=session_auth_headers("alice"),
        )
        assert set_.status_code == 200, set_.text
    if closed:
        done = _close(client, task, session_auth_headers("alice"))
        assert done.status_code == 200, done.text
    return task, room


def _close(client, task, headers):
    return client.post(
        f"/topics/{task['id']}/close", json={"conclusion": "先到这里"}, headers=headers
    )


def _reopen(client, task, headers):
    return client.post(f"/topics/{task['id']}/reopen", headers=headers)


def _status(client, task) -> str:
    return client.get(
        f"/topics/{task['id']}/task", headers=session_auth_headers("alice")
    ).json()["data"]["status"]


# —— reopen ————————————————————————————————————————————————————————————


def test_the_owner_reopens_it(client):
    task, _room = _task(client, closed=True)

    reopened = _reopen(client, task, session_auth_headers("alice"))

    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["data"]["status"] == "open"


def test_a_member_who_does_not_work_it_reopens_it(client):
    task, _room = _task(client, closed=True)

    reopened = _reopen(client, task, session_auth_headers("bob"))

    assert reopened.status_code == 200, reopened.text
    assert _status(client, task) == "open"


def test_a_mentor_does_not_reopen_it(client):
    task, _room = _task(client, closed=True)

    refused = _reopen(client, task, session_auth_headers("teacher"))

    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "taskOwnSessionReopens"
    assert _status(client, task) == "closed"


def test_the_tasks_own_session_reopens_it(client):
    task, room = _task(client, closed=True)
    seat = room_agent_seat(client, room)

    reopened = _reopen(
        client,
        task,
        task_agent_headers(task["project_id"], uuid.UUID(task["id"]), seat),
    )

    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["data"]["status"] == "open"


# —— close ——————————————————————————————————————————————————————————————


def test_the_owner_closes_it(client):
    task, _room = _task(client, closed=False)

    closed = _close(client, task, session_auth_headers("alice"))

    assert closed.status_code == 200, closed.text
    assert _status(client, task) == "closed"


def test_a_contributor_closes_it(client):
    task, _room = _task(client, closed=False, contributors=("bob",))

    closed = _close(client, task, session_auth_headers("bob"))

    assert closed.status_code == 200, closed.text
    assert _status(client, task) == "closed"


def test_a_member_who_does_not_work_it_does_not_close_it(client):
    task, _room = _task(client, closed=False)

    refused = _close(client, task, session_auth_headers("bob"))

    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "taskOwnSessionCloses"
    assert _status(client, task) == "open"


# —— sessions that are not the task's own, for both ————————————————————————

_ACTIONS = [
    pytest.param(True, _reopen, "closed", "taskOwnSessionReopens", id="reopen"),
    pytest.param(False, _close, "open", "taskOwnSessionCloses", id="close"),
]


@pytest.mark.parametrize(("closed", "act", "stays", "_key"), _ACTIONS)
def test_another_tasks_session_does_not(client, closed, act, stays, _key):
    task, room = _task(client, closed=closed)
    other = open_task(client, room, "别的活", owner="alice")
    seat = room_agent_seat(client, room)

    refused = act(
        client,
        task,
        task_agent_headers(task["project_id"], uuid.UUID(other["id"]), seat),
    )

    assert refused.status_code in (403, 404), refused.text
    assert _status(client, task) == stays


@pytest.mark.parametrize(("closed", "act", "stays", "key"), _ACTIONS)
def test_the_rooms_session_does_not(client, closed, act, stays, key):
    """The room's session-wide credential reaches every task of its project, so
    it is the one that gets past the conversation check to the rule itself."""
    task, room = _task(client, closed=closed)
    token = mint_scoped_token(
        project_id=task["project_id"],
        topic_id=room,
        agent_handle=room_agent_seat(client, room),
        access_scope="project",
    )

    refused = act(client, task, {"Authorization": f"Bearer {token}"})

    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["i18n"]["key"] == key
    assert _status(client, task) == stays


def test_a_contributor_closing_it_does_not_change_who_is_credited(client):
    """Who reported and who works the task is the owner's to change
    (`update_task`), and closing is no way around that."""
    task, _room = _task(client, closed=False, contributors=("bob",))

    refused = client.post(
        f"/topics/{task['id']}/close",
        json={
            "conclusion": "先到这里",
            "reporter_handle": "bob",
            "contributor_handles": [],
        },
        headers=session_auth_headers("bob"),
    )

    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "taskOwnerOnly"
    after = client.get(
        f"/topics/{task['id']}/task", headers=session_auth_headers("alice")
    ).json()["data"]
    assert after["status"] == "open"
    assert after["contributor_handles"] == ["bob"]
