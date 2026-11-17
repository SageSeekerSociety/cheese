"""A task's own AI teammate closes it only on grounds the platform can check
(`review.close_grounds`): its last delivery landed and its checklist is done
(or it says why not), or the owner said in the task that it is over.

The close it got wrong is what ended tasks their owners still worked in: a
prototype shown and the task closed (「发题做题改题权限调研」), a merge that
was one step of several. People close a task with no grounds asked of them.
"""

import uuid

import pytest

from app.domain.topic.repositories.progress import TopicProgressRepository
from tests.integration.conftest import (
    post_message,
    session_auth_headers,
    task_agent_headers,
)
from tests.integration.test_a_task_delivers_in_steps import _accepted_step
from tests.integration.test_accept_pr import (
    app_world as _app_world_fixture,
)
from tests.landing import teammate_seat

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]


def _room_task(client) -> dict:
    from tests.integration.test_tasks import _room

    _project, room = _room(client)
    said = post_message(client, room, "alice", {"content": "把报名页的原型画出来"})
    return client.post(
        f"/blocks/{said['id']}/upgrade", headers=session_auth_headers("alice")
    ).json()["data"]


def _teammate_closes(client, task_id: str, **body):
    project_id, handle = teammate_seat(client, task_id)
    return client.post(
        f"/topics/{task_id}/close",
        json={"conclusion": "做完了", **body},
        headers=task_agent_headers(project_id, task_id, handle),
    )


def _checklist(client, task_id: str, *statuses: str) -> None:
    async def save():
        async with client.test_factory() as db:
            await TopicProgressRepository(db).save(
                uuid.UUID(str(task_id)),
                [
                    {"id": str(n), "subject": f"第 {n} 步", "status": status}
                    for n, status in enumerate(statuses, start=1)
                ],
            )
            await db.commit()

    client.portal.call(save)


def _error_key(response) -> str:
    return response.json()["error"]["i18n"]["key"]


def test_a_task_with_nothing_delivered_is_not_closed_by_its_teammate(client):
    task = _room_task(client)

    refused = _teammate_closes(client, task["id"])

    assert refused.status_code == 409, refused.text
    assert _error_key(refused) == "taskCloseNeedsGrounds"
    assert client.get(f"/topics/{task['id']}/task").json()["data"]["status"] == "open"


def test_the_owner_saying_it_is_over_lets_the_teammate_close_it(client):
    task = _room_task(client)
    said = post_message(client, task["id"], "alice", {"content": "原型可以了，收了吧"})

    closed = _teammate_closes(client, task["id"], owner_message_id=said["id"])

    assert closed.status_code == 200, closed.text
    assert client.get(f"/topics/{task['id']}/task").json()["data"]["status"] == "closed"


def _said_by(client, task: dict, author: str, content: str) -> str:
    from app.domain.block.models import Block, BlockKind
    from app.domain.event_author import AuthorType

    block_id = uuid.uuid4()

    async def write():
        async with client.test_factory() as db:
            db.add(
                Block(
                    id=block_id,
                    project_id=uuid.UUID(task["project_id"]),
                    conversation_id=uuid.UUID(task["id"]),
                    kind=BlockKind.message,
                    author_type=AuthorType.participant,
                    author=author,
                    content=content,
                )
            )
            await db.commit()

    client.portal.call(write)
    return str(block_id)


def test_someone_else_saying_so_is_not_the_owners_word(client):
    task = _room_task(client)
    said = _said_by(client, task, "bob", "可以关了")

    refused = _teammate_closes(client, task["id"], owner_message_id=said)

    assert refused.status_code == 409, refused.text
    assert _error_key(refused) == "taskCloseOwnerMessageNotFound"


def test_the_owner_closes_it_with_no_grounds(client):
    task = _room_task(client)

    closed = client.post(
        f"/topics/{task['id']}/close",
        json={"conclusion": "不做了"},
        headers=session_auth_headers("alice"),
    )

    assert closed.status_code == 200, closed.text


def test_a_landed_delivery_with_steps_left_is_not_closed(client, app_world):
    task_id, _ = _accepted_step(client, app_world)
    _checklist(client, task_id, "completed", "pending")

    refused = _teammate_closes(client, task_id)

    assert refused.status_code == 409, refused.text
    assert _error_key(refused) == "taskCloseChecklistOpen"
    assert "第 2 步" in refused.json()["error"]["message"]


def test_steps_left_with_a_reason_close_and_the_reason_is_kept(client, app_world):
    task_id, _ = _accepted_step(client, app_world)
    _checklist(client, task_id, "completed", "pending")

    closed = _teammate_closes(
        client, task_id, unchecked_reason="负责人改成下个任务再做"
    )

    assert closed.status_code == 200, closed.text
    task = client.get(f"/topics/{task_id}/task").json()["data"]
    assert task["status"] == "closed"
    assert "第 2 步" in task["conclusion"]
    assert "负责人改成下个任务再做" in task["conclusion"]


def test_a_landed_delivery_with_its_checklist_done_closes(client, app_world):
    task_id, _ = _accepted_step(client, app_world)
    _checklist(client, task_id, "completed", "completed")

    closed = _teammate_closes(client, task_id)

    assert closed.status_code == 200, closed.text
