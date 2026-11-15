"""A task's deliveries are told in the conversation it was opened from.

The rules, as a person would state them:

- When a version is handed in for review, the conversation the task came
  from (the channel's main line, or the 支线 it came from) records it, with
  the card it filed and which version. The record never says how the review
  stands; it records that the version went in.
- When a version is returned, the same conversation records who returned it.
  Handed in again, it is the next version.
- Those two are things that happened, recorded by the platform: they are not
  words of the task's AI teammate, appear under nobody's name, and start no
  turn of the channel's AI teammate.
- Once the task is over, the channel hears how it ended exactly once, from
  the task's teammate: the write-up when it writes one, or that it was
  accepted when the write-up never comes.
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.domain.agent.models import AgentTurn
from app.domain.room_task.models import Task
from tests.delivery import delivery_headers, delivery_task, delivery_task_id
from tests.integration.conftest import (
    in_thread,
    join_project_team,
    post_message,
    session_auth_headers,
)
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


def _told(client, conversation: str, task_id: uuid.UUID) -> list[dict]:
    """What ``conversation`` holds about ``task_id``: what happened to it, and
    what its teammate said there."""
    blocks = client.get(
        f"/topics/{conversation}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    return [
        b
        for b in blocks
        if str(task_id)
        in (
            (b.get("meta") or {}).get("task_id"),
            (b.get("meta") or {}).get("task_result"),
        )
    ]


def _happened(line: dict) -> bool:
    """A line the platform recorded, drawn as something that happened: under
    nobody's name."""
    return (
        line["kind"] == "event"
        and line["author_type"] == "platform"
        and line["shown_as"] == "happening"
    )


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
    # Each line carries its sentence, so every reader sees it in their language.
    assert [line["meta"]["i18n"]["content"]["key"] for line in lines] == [
        "taskFiledForReview",
        "taskReturned",
        "taskFiledForReview",
    ]
    assert all(_happened(line) for line in lines)
    assert [line["meta"].get("delivery") for line in lines] == [
        {"card_id": first, "version": 1},
        None,
        {"card_id": second, "version": 2},
    ]
    assert "已提交审阅" in lines[0]["content"]
    assert "<@alice>" in lines[1]["content"]
    assert "再短一点" not in lines[1]["content"]
    assert _turns(client, room) == turns


def test_a_task_from_a_thread_is_recorded_in_that_thread(client, app_world):
    room = _make_topic(client, _make_project(client))
    thread = in_thread(client, room, "alice")
    task = delivery_task(client, room)

    async def hang():
        from app.domain.block.repositories import BlockRepository
        from app.domain.event_author import AuthorType
        from app.domain.thread.models import Thread

        async with client.test_factory() as session:
            reply = await BlockRepository(session).add(
                project_id=task.project_id,
                conversation_id=uuid.UUID(thread),
                author="alice",
                author_type=AuthorType.participant,
                content="把报名表做短",
            )
            root = (await session.get(Thread, uuid.UUID(thread))).root_block_id
            await session.commit()
        async with client.test_factory() as session:
            row = await session.get(Task, task.id)
            row.upgraded_from_block_id = root
            row.source_block_id = reply.id
            await session.commit()

    client.portal.call(hang)
    card = _file(client, room)
    returned = client.post(
        f"/accept-cards/{card}/reject",
        json={"note": "", "decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert returned.status_code == 200, returned.text

    lines = _told(client, thread, task.id)
    assert [line["meta"]["i18n"]["content"]["key"] for line in lines] == [
        "taskFiledForReview",
        "taskReturned",
    ]
    assert all(_happened(line) for line in lines)
    assert lines[0]["meta"]["delivery"] == {"card_id": card, "version": 1}
    assert _told(client, room, task.id) == []


def _card_on_rows(client, room: str, task_id: uuid.UUID, viewer: str) -> list[dict]:
    """The card of ``task_id`` as each list a reader sees it on carries it: the
    channel's open tasks, and the task's own page."""
    listed = client.get(
        f"/topics/{room}/tasks",
        params={"status": "open", "limit": 0},
        headers=session_auth_headers(viewer),
    ).json()["data"]["data"]
    page = client.get(
        f"/topics/{task_id}/task", headers=session_auth_headers(viewer)
    ).json()["data"]
    [row] = [row for row in listed if row["id"] == str(task_id)]
    return [row["card"], page["card"]]


def test_a_card_waiting_for_review_names_its_reviewers_wherever_it_is_listed(
    client, app_world
):
    """The channel's 「待你审阅」 reads whom a pending card waits on from the
    list: every reviewer of it is named there, as on the task page, and the
    task's owner who is not one is not."""
    project = _make_project(client)
    join_project_team(client, project, "carol")
    room = _make_topic(client, project)
    task_id = _opened_in_the_main_line(client, room)
    r = client.post(
        f"/topics/{delivery_task_id(client, room)}/accept-card",
        headers=delivery_headers(client, room),
        json={"change_subject": _SUBJECT, "reviewer_handles": ["bob", "carol"]},
    )
    assert r.status_code == 200, r.text

    # alice owns the task and is not asked to review it.
    for card in _card_on_rows(client, room, task_id, "alice"):
        assert card["status"] == "pending"
        assert sorted((card["reviewer"] or "").split("、")) == ["bob", "carol"]


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
    # The ending is the teammate's own words, said under its name.
    assert ending["kind"] == "message"
    assert ending["author_type"] == "participant"


def test_a_task_whose_write_up_never_comes_still_ends_in_the_main_line(
    client, app_world
):
    room, task_id = _accepted_last_step(client, app_world)

    time_passes(client, str(task_id), minutes=60)
    watch(client)

    *_, ending = _told(client, room, task_id)
    assert len(_told(client, room, task_id)) == 2
    assert "已采纳" in ending["content"]
