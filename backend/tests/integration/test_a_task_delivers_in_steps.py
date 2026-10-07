"""A task delivers in steps: only the step its card calls the last closes it.

A plan of three steps used to end at the first: accepting step one closed the
task, and the other two had nowhere to go (dev, 2026-10-07).
"""

import pytest
from sqlalchemy import text

from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import post_message, session_auth_headers
from tests.integration.test_accept_pr import (
    _accept,
    _give_card_a_pr,
    _make_project,
    _make_topic,
)
from tests.integration.test_accept_pr import (
    app_world as _app_world_fixture,
)

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]


def _file(client, topic_id: str, *, completes_task: bool) -> str:
    r = client.post(
        f"/topics/{delivery_task_id(client, topic_id)}/accept-card",
        headers=delivery_headers(client, topic_id),
        json={
            "change_subject": "feat(form): trim the sign-up form",
            "reviewer_handle": "alice",
            "completes_task": completes_task,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["completes_task"] is completes_task
    return r.json()["data"]["id"]


def _accepted_step(client, app_world, *, completes_task: bool) -> tuple[str, dict]:
    fake = app_world["fake"]
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _file(client, tid, completes_task=completes_task)
    head = _give_card_a_pr(client, app_world, tid, cid, 7)
    fake.check_state_by_sha[head] = ("success", "全部通过")
    fake.merge_sha_by_number[7] = "merge-sha-1"
    task_id = delivery_task_id(client, tid)
    before = client.get(f"/topics/{task_id}/task").json()["data"]
    r = _accept(client, cid)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "accepted"
    return task_id, before


def _told(client, task_id: str) -> str:
    async def read():
        async with client.test_factory() as db:
            rows = await db.execute(
                text(
                    "SELECT payload->>'content' FROM deliveries "
                    "WHERE conversation_id = :task ORDER BY recorded_at"
                ),
                {"task": task_id},
            )
            return "\n".join(content or "" for (content,) in rows)

    return client.portal.call(read)


def test_accepting_a_step_that_is_not_the_last_keeps_the_task_going(client, app_world):
    task_id, before = _accepted_step(client, app_world, completes_task=False)

    task = client.get(f"/topics/{task_id}/task").json()["data"]
    assert task["status"] == "open"
    assert task["presentation"]["phrase"] != "accepted"
    # The next step is made on a branch of its own, from the latest code, and
    # opens a PR of its own.
    assert task["branch_name"] != before["branch_name"]
    assert task["pr_number"] is None
    assert "接着做下一步" in _told(client, task_id)


def test_accepting_the_last_step_completes_the_task(client, app_world):
    task_id, _ = _accepted_step(client, app_world, completes_task=True)

    task = client.get(f"/topics/{task_id}/task").json()["data"]
    assert task["status"] == "closed"
    assert task["presentation"]["phrase"] == "accepted"


def test_the_owner_reopens_a_completed_task_onto_the_latest_code(client, app_world):
    task_id, before = _accepted_step(client, app_world, completes_task=True)
    owner = client.get(f"/topics/{task_id}/task").json()["data"]["owner_handle"]

    refused = client.post(
        f"/topics/{task_id}/reopen", headers=session_auth_headers("someone-else")
    )
    assert refused.status_code in (403, 404)

    r = client.post(f"/topics/{task_id}/reopen", headers=session_auth_headers(owner))
    assert r.status_code == 200, r.text
    task = r.json()["data"]
    assert task["status"] == "open"
    assert task["branch_name"] != before["branch_name"]
    assert task["pr_number"] is None


def test_an_open_task_cannot_be_reopened(client):
    from tests.integration.test_tasks import _room

    _project, room = _room(client)
    task = client.post(
        f"/topics/{room}/tasks",
        json={"title": "一件事"},
        headers=session_auth_headers("alice"),
    ).json()["data"]

    r = client.post(
        f"/topics/{task['id']}/reopen", headers=session_auth_headers("alice")
    )

    assert 400 <= r.status_code < 500
    assert client.get(f"/topics/{task['id']}/task").json()["data"]["status"] == "open"


def test_how_a_task_ended_is_said_where_it_came_from(client):
    from tests.integration.test_tasks import _room

    _project, room = _room(client)
    said = post_message(client, room, "alice", {"content": "报名表单太长了"})
    task = client.post(
        f"/blocks/{said['id']}/upgrade", headers=session_auth_headers("alice")
    ).json()["data"]

    closed = client.post(
        f"/topics/{task['id']}/close",
        json={"conclusion": "必填项压到 4 项"},
        headers=session_auth_headers("alice"),
    )
    assert closed.status_code == 200, closed.text

    threads = client.get(f"/topics/{room}/threads").json()["data"]
    [thread] = [t for t in threads if t["root_block_id"] == said["id"]]
    replies = client.get(f"/topics/{thread['id']}/blocks").json()["data"]["data"]
    assert any("必填项压到 4 项" in b["content"] for b in replies)
