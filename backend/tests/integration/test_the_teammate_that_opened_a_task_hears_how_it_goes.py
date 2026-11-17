"""The AI teammate that opened a task from the channel's main line is told when
the task hands a version in and when it ends, to say so there itself.

The rules, as a person would state them:

- A task an AI teammate opened from something said in the channel's main line:
  when it hands a version in for review, that teammate is told, in the main
  line, which task, which version, who reviews it, what was handed in and what
  to look at. Each version is told once; the next version is told again.
- A version returned is not told to it.
- When the task closes, that teammate is told how it ended — written up,
  accepted with no write-up, or closed with nothing accepted — whichever way it
  closed: by a person, by its own session after the write-up, by the platform
  when the write-up never comes or never starts. Each end is told once; a task
  reopened and closed again ends again.
- Nobody is told for a task a person made, or one that came from a 支线.
- How the task ended is recorded where it came from as something that
  happened, under nobody's name, not as words of an AI teammate.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.api.deps import get_chat_service
from app.domain.agent.announce import announce
from app.domain.delivery.agent import record_seat_instruction
from app.domain.delivery.models import Delivery
from app.domain.delivery.timer import give_up_stale
from app.domain.review.models import AcceptCard
from app.domain.review.task_landing import tell_opener_filed
from app.domain.room_task.closing import CLOSES_TASK
from app.domain.room_task.models import Task
from app.domain.user.models import User, UserProfile
from tests.delivery import delivery_headers, delivery_task
from tests.integration.conftest import (
    in_thread,
    post_message,
    room_agent_seat,
    session_auth_headers,
)
from tests.integration.test_a_delivery_is_told_where_the_task_came_from import (
    _SUBJECT,
    _file,
    _happened,
    _told,
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


def _opened(client, room: str, *, by_teammate: bool = True) -> uuid.UUID:
    """The room's task, opened from a message in the channel's main line: by
    the room's AI teammate, which records its seat as who opened it, or by a
    person."""
    asked = post_message(client, room, "alice", {"content": "把报名表做短"})
    task = delivery_task(client, room)
    opener = room_agent_seat(client, room) if by_teammate else "alice"

    async def hang():
        async with client.test_factory() as session:
            row = await session.get(Task, task.id)
            row.upgraded_from_block_id = row.source_block_id = uuid.UUID(asked["id"])
            row.created_by = opener
            await session.commit()

    client.portal.call(hang)
    return task.id


def _told_opener(client, room: str) -> list[str]:
    """What waits in the channel's main line for its AI teammate, oldest first."""
    seat = room_agent_seat(client, room)

    async def read():
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(Delivery.payload["content"].astext)
                    .where(
                        Delivery.conversation_id == uuid.UUID(room),
                        Delivery.recipient_handle == seat,
                    )
                    .order_by(Delivery.recorded_at)
                )
            )

    return client.portal.call(read)


def _goes_by(client, handle: str, nickname: str) -> None:
    """``handle`` is shown by ``nickname`` on their profile."""

    async def name():
        async with client.test_factory() as session:
            user_id = await session.scalar(
                select(User.id).where(User.username == handle)
            )
            profile = await session.scalar(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )
            if profile is None:
                now = datetime.now(UTC)
                session.add(
                    UserProfile(
                        user_id=user_id,
                        nickname=nickname,
                        intro="",
                        avatar_id=0,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                profile.nickname = nickname
            await session.commit()

    client.portal.call(name)


def _endings(client, room: str, task_id: uuid.UUID) -> list[dict]:
    """The lines where the task came from that record how it ended."""
    return [
        line
        for line in _told(client, room, task_id)
        if line["meta"].get("event_type") == "task_ended"
    ]


def _close(client, task_id: uuid.UUID, conclusion: str | None = None):
    r = client.post(
        f"/topics/{task_id}/close",
        json={"conclusion": conclusion} if conclusion else {},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text


def test_each_version_handed_in_is_told_once_and_a_return_is_not(client, app_world):
    room = _make_topic(client, _make_project(client))
    _opened(client, room)
    _goes_by(client, "alice", "爱丽丝")
    focus_card = client.post(
        f"/topics/{delivery_task(client, room).id}/accept-card",
        headers=delivery_headers(client, room),
        json={
            "change_subject": _SUBJECT,
            "change_body": "报名表只留姓名和学号",
            "focus": "手机上看一眼表单",
            "reviewer_handle": "alice",
        },
    )
    assert focus_card.status_code == 200, focus_card.text
    first = focus_card.json()["data"]["id"]

    [told] = _told_opener(client, room)
    assert "Test delivery" in told
    assert "第 1 版" in told
    assert "爱丽丝" in told
    assert _SUBJECT in told and "报名表只留姓名和学号" in told
    assert "手机上看一眼表单" in told

    returned = client.post(
        f"/accept-cards/{first}/reject",
        json={"note": "再短一点", "decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert returned.status_code == 200, returned.text
    assert len(_told_opener(client, room)) == 1

    _file(client, room)
    *_, second = _told_opener(client, room)
    assert len(_told_opener(client, room)) == 2
    assert "第 2 版" in second
    # No focus was given this time, so there is no sentence for it.
    assert "审阅重点" not in second


def test_the_same_version_reported_again_is_not_told_again(client, app_world):
    room = _make_topic(client, _make_project(client))
    task_id = _opened(client, room)
    card = _file(client, room)

    async def again():
        async with client.test_factory() as session:
            await tell_opener_filed(
                session,
                await session.get(Task, task_id),
                await session.get(AcceptCard, uuid.UUID(card)),
                version=1,
                reviewers=["alice"],
                record_seat=record_seat_instruction,
            )
            await session.commit()

    client.portal.call(again)
    assert len(_told_opener(client, room)) == 1


def test_a_task_a_person_made_wakes_nobody(client, app_world):
    room = _make_topic(client, _make_project(client))
    task_id = _opened(client, room, by_teammate=False)
    _file(client, room)
    _close(client, task_id, "不做了")
    assert _told_opener(client, room) == []


def test_a_task_from_a_thread_wakes_nobody(client, app_world):
    room = _make_topic(client, _make_project(client))
    thread = in_thread(client, room, "alice")
    task = delivery_task(client, room)
    seat = room_agent_seat(client, room)

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
            row.created_by = seat
            await session.commit()

    client.portal.call(hang)
    _file(client, room)
    _close(client, task.id, "不做了")
    assert _told_opener(client, room) == []
    # The thread still records how it ended, as something that happened.
    [*_, ending] = _told(client, thread, task.id)
    assert _happened(ending)
    assert ending["meta"]["event_type"] == "task_ended"


def test_a_task_closed_by_a_person_is_told_once_and_again_after_a_reopen(
    client, app_world
):
    room = _make_topic(client, _make_project(client))
    task_id = _opened(client, room)

    _close(client, task_id, "改用纸质表")
    [ended] = _told_opener(client, room)
    assert "Test delivery" in ended
    assert "改用纸质表" in ended
    # Nothing was accepted, and the words say so rather than that it was done.
    assert "没有哪一版交付被采纳" in ended

    reopened = client.post(
        f"/topics/{task_id}/reopen", headers=session_auth_headers("alice")
    )
    assert reopened.status_code == 200, reopened.text
    _close(client, task_id)
    told = _told_opener(client, room)
    assert len(told) == 2
    assert "也没有写结论" in told[1]

    # Its end is recorded where it came from as something that happened, each
    # time, and no AI teammate says it there.
    endings = _endings(client, room, task_id)
    assert len(endings) == 2
    assert all(_happened(line) for line in endings)
    assert not [
        line for line in _told(client, room, task_id) if line["kind"] == "message"
    ]


def _accepted_last_step(client, app_world) -> tuple[str, uuid.UUID]:
    fake = app_world["fake"]
    room = _make_topic(client, _make_project(client))
    task_id = _opened(client, room)
    card = _file(client, room)
    head = _give_card_a_pr(client, app_world, room, card, 7)
    fake.check_state_by_sha[head] = ("success", "全部通过")
    fake.merge_sha_by_number[7] = "merge-sha-1"
    accepted = _accept(client, card)
    assert accepted.status_code == 200, accepted.text
    return room, task_id


def test_a_task_its_session_wrote_up_is_told_with_its_conclusion(client, app_world):
    room, task_id = _accepted_last_step(client, app_world)
    assert len(_told_opener(client, room)) == 1  # the version handed in

    written_up(client, str(task_id))

    *_, ended = _told_opener(client, room)
    assert len(_told_opener(client, room)) == 2
    assert "交付已采纳" in ended
    assert "做完了" in ended
    # Recorded where it came from in each reader's language, under nobody's name.
    [line] = _endings(client, room, task_id)
    assert _happened(line)
    assert line["meta"]["i18n"]["content"]["key"] == "taskCompleted"


def test_a_task_whose_write_up_never_comes_is_told_once(client, app_world):
    room, task_id = _accepted_last_step(client, app_world)

    time_passes(client, str(task_id), minutes=60)
    watch(client)
    watch(client)

    *_, ended = _told_opener(client, room)
    assert len(_told_opener(client, room)) == 2
    assert "交付已采纳" in ended
    assert "总结没有写成" in ended


def test_a_task_whose_write_up_never_starts_is_told_once(client, app_world):
    room, task_id = _accepted_last_step(client, app_world)
    chat = client.app.dependency_overrides[get_chat_service]()

    async def never_started():
        async with client.test_factory() as session:
            await session.execute(
                update(Delivery)
                .where(
                    Delivery.conversation_id == task_id,
                    Delivery.payload[CLOSES_TASK].as_boolean().is_(True),
                )
                .values(
                    attempts=1,
                    recorded_at=datetime.now(UTC) - timedelta(minutes=31),
                )
            )
            await session.commit()

    client.portal.call(never_started)
    client.portal.call(
        lambda: give_up_stale(client.test_factory, chat=chat, announce=announce)
    )
    client.portal.call(
        lambda: give_up_stale(client.test_factory, chat=chat, announce=announce)
    )

    *_, ended = _told_opener(client, room)
    assert len(_told_opener(client, room)) == 2
    assert "总结没有写成" in ended
    [line] = _endings(client, room, task_id)
    assert _happened(line)
