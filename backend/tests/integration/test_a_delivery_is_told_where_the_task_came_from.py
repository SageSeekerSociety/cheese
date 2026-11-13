"""A task's deliveries are told in the conversation it was opened from.

The rules, as a person would state them:

- When a version is handed in for review, the channel the task came from gets
  one line saying so, with the card it filed: what was handed in, and which
  version. The line never says how the review stands; it records that the
  version went in. It starts no turn of the channel's AI teammate.
- When a version is returned, the channel gets one line saying who returned
  it. Handed in again, it is the next version.
- Once the task is over, the channel hears how it ended exactly once: the
  write-up when the task's teammate writes one, or that it was accepted when
  the write-up never comes.
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.domain.agent.models import AgentTurn
from app.domain.room_task.models import Task
from tests.delivery import delivery_headers, delivery_task, delivery_task_id
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
from tests.landing import time_passes, watch, written_up

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]

_SUBJECT = "feat(form): trim the sign-up form"


def _opened_in_the_main_line(client, room: str) -> uuid.UUID:
    """The room's task, opened from a message in the channel's main line."""
    asked = post_message(client, room, "alice", {"content": "把报名表做短"})
    task = delivery_task(client, room)

    async def hang():
        async with client.test_factory() as session:
            row = await session.get(Task, task.id)
            row.upgraded_from_block_id = row.source_block_id = uuid.UUID(asked["id"])
            await session.commit()

    client.portal.call(hang)
    return task.id


def _file(client, room: str, *, completes_task: bool = True) -> str:
    r = client.post(
        f"/topics/{delivery_task_id(client, room)}/accept-card",
        headers=delivery_headers(client, room),
        json={
            "change_subject": _SUBJECT,
            "reviewer_handle": "alice",
            "completes_task": completes_task,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _told(client, room: str, task_id: uuid.UUID) -> list[dict]:
    blocks = client.get(
        f"/topics/{room}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    return [
        b for b in blocks if (b.get("meta") or {}).get("task_result") == str(task_id)
    ]


def _turns(client, room: str) -> int:
    async def count():
        async with client.test_factory() as session:
            return await session.scalar(
                select(func.count())
                .select_from(AgentTurn)
                .where(AgentTurn.conversation_id == uuid.UUID(room))
            )

    return client.portal.call(count)


def test_handing_in_and_returning_are_told_in_the_main_line(client, app_world):
    room = _make_topic(client, _make_project(client))
    task_id = _opened_in_the_main_line(client, room)
    turns = _turns(client, room)

    first = _file(client, room)
    returned = client.post(
        f"/accept-cards/{first}/reject",
        json={"note": "再短一点", "decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert returned.status_code == 200, returned.text
    second = _file(client, room)

    lines = _told(client, room, task_id)
    assert [line["meta"].get("delivery") for line in lines] == [
        {"card_id": first, "subject": _SUBJECT, "version": 1},
        None,
        {"card_id": second, "subject": _SUBJECT, "version": 2},
    ]
    assert "已提交审阅" in lines[0]["content"]
    assert "退回" in lines[1]["content"]
    assert "再短一点" not in lines[1]["content"]
    assert _turns(client, room) == turns


def _accepted_last_step(client, app_world) -> tuple[str, uuid.UUID]:
    fake = app_world["fake"]
    room = _make_topic(client, _make_project(client))
    task_id = _opened_in_the_main_line(client, room)
    card = _file(client, room)
    head = _give_card_a_pr(client, app_world, room, card, 7)
    fake.check_state_by_sha[head] = ("success", "全部通过")
    fake.merge_sha_by_number[7] = "merge-sha-1"
    accepted = _accept(client, card)
    assert accepted.status_code == 200, accepted.text
    return room, task_id


def test_a_written_up_task_ends_in_the_main_line_once(client, app_world):
    room, task_id = _accepted_last_step(client, app_world)
    # Accepted, and being written up: nothing is said past the hand-in yet.
    [handed_in] = _told(client, room, task_id)
    assert handed_in["meta"].get("delivery") is not None

    written_up(client, str(task_id))

    *_, ending = _told(client, room, task_id)
    assert len(_told(client, room, task_id)) == 2
    assert "做完了" in ending["content"]


def test_a_task_whose_write_up_never_comes_still_ends_in_the_main_line(
    client, app_world
):
    room, task_id = _accepted_last_step(client, app_world)

    time_passes(client, str(task_id), minutes=60)
    watch(client)

    *_, ending = _told(client, room, task_id)
    assert len(_told(client, room, task_id)) == 2
    assert "已采纳" in ending["content"]
