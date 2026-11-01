"""Changes to a task, and messages sent in it, never abort each other.

A message sent in a task, and every change to the task — closing it, the
platform closing it after its write-up, giving it to another teammate, handing
it to another owner, changing who works it, archiving its project — each runs
in one transaction. Run at the same moment, any two of them finish: one waits
for the other, then goes on (or is refused for what it now reads, as a 4xx).
None of them fails because the two waited on each other.

The same holds for two transactions that each change several tasks, the same
ones, reaching them in opposite orders (archiving a project room by room, the
overdue sweep task by task).

Each case starts one operation, holds it at the point where it is under way in
the task, starts the other and lets it go as far as it can, then lets the
first one finish.
"""

import asyncio
import itertools
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.api import thread_task
from app.api.deps import get_work_runner
from app.api.routes import topics_tasks
from app.core.config import settings
from app.domain.agent.turn.intake import human
from app.domain.block.adapters import platform_line
from app.domain.block.repositories import BlockRepository
from app.domain.conversation.numbering import NumberingOutOfOrder
from app.domain.event_about import EventAbout
from app.domain.event_author import AuthorType
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.room_task import closing, heard
from app.domain.room_task.lock_order import lock_task_for_change
from app.domain.room_task.models import Task
from app.domain.room_task.services import TaskService
from app.domain.topic.services import TopicService
from tests.integration.conftest import (
    join_project_team,
    post_message,
    session_auth_headers,
)
from tests.integration.test_the_composer_shows_who_a_message_is_for import (
    _channel,
    _task_with_a_turn,
)


class _Hold:
    """The first call through the patched function waits here until released;
    later calls, the other operation's included, pass straight through."""

    def __init__(self) -> None:
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self._armed = True

    def around(self, original):
        async def held(*args, **kwargs):
            if self._armed:
                self._armed = False
                self.reached.set()
                await self.release.wait()
            return await original(*args, **kwargs)

        return held


def _post(client, path, body):
    return asyncio.to_thread(
        client.post, path, json=body, headers=session_auth_headers("alice")
    )


def _patch(client, path, body):
    return asyncio.to_thread(
        client.patch, path, json=body, headers=session_auth_headers("alice")
    )


async def _close_after_summary(client, task):
    async with client.test_factory() as db:
        closed = await closing.close_after_summary(db, uuid.UUID(task), None)
        await db.commit()
        return closed


# Each operation: how it runs, and where it is held when it goes first — a
# point it reaches once it is under way in the task.
OPERATIONS = {
    "send": (
        lambda c, t, p: _post(
            c,
            f"/topics/{t}/messages",
            {
                "request_id": str(uuid.uuid4()),
                "content": "同时说一句",
                "expects_agent": True,
            },
        ),
        (human, "prepare_human"),
    ),
    "close": (
        lambda c, t, p: _post(c, f"/topics/{t}/close", {}),
        (topics_tasks, "close_cards_for_closed_task"),
    ),
    "close after the write-up": (
        lambda c, t, p: _close_after_summary(c, t),
        (closing, "unfinished"),
    ),
    "teammate given back": (
        lambda c, t, p: _patch(c, f"/topics/{t}/task", {"agent_handle": None}),
        (TaskService, "give_agent"),
    ),
    "owner handed over": (
        lambda c, t, p: _patch(c, f"/topics/{t}/task", {"owner_handle": "bob"}),
        (TaskService, "hand_over"),
    ),
    "collaborators changed": (
        lambda c, t, p: _patch(
            c, f"/topics/{t}/task", {"contributor_handles": ["carol"]}
        ),
        (heard, "inform"),
    ),
    "project archived": (
        lambda c, t, p: _post(c, f"/projects/{p}/archive", {}),
        (TopicService, "_archive_children"),
    ),
}


def _task_under_way(client) -> tuple[str, str, str]:
    """An open task of alice's with its teammate named, waiting for its
    write-up, a card riding an unmerged PR, and bob and carol in the project.
    Returns the project, the task and the teammate's seat."""
    project, _, room = _channel(client)
    task, seat = _task_with_a_turn(client, room)
    join_project_team(client, project, "bob")
    join_project_team(client, project, "carol")

    async def seed():
        async with client.test_factory() as db:
            await db.execute(
                update(Task)
                .where(Task.id == uuid.UUID(task))
                .values(agent_handle=seat, closing_since=datetime.now(UTC))
            )
            db.add(
                AcceptCard(
                    topic_id=uuid.UUID(room),
                    task_id=uuid.UUID(task),
                    status=AcceptStatus.pending,
                    pr_number=1,
                    pr_url="https://example.invalid/pr/1",
                )
            )
            await db.commit()

    client.portal.call(seed)
    return project, task, seat


def _finished(result) -> None:
    assert not isinstance(result, BaseException), repr(result)
    status = getattr(result, "status_code", None)
    assert status is None or status < 500, result.text


@pytest.mark.parametrize(
    ("first", "then"),
    [pair for pair in itertools.permutations(OPERATIONS, 2)],
    ids=lambda name: name.replace(" ", "-"),
)
def test_two_operations_on_one_task_both_finish(
    client, stub_hooks, monkeypatch, first, then
):
    project, task, _ = _task_under_way(client)
    run_first, (owner, name) = OPERATIONS[first]
    run_then, _ = OPERATIONS[then]
    hold = _Hold()
    monkeypatch.setattr(owner, name, hold.around(getattr(owner, name)))

    async def race():
        one = asyncio.ensure_future(run_first(client, task, project))
        await asyncio.wait_for(hold.reached.wait(), 10)
        two = asyncio.ensure_future(run_then(client, task, project))
        await asyncio.sleep(0.5)
        hold.release.set()
        return await asyncio.wait_for(
            asyncio.gather(one, two, return_exceptions=True), 20
        )

    for result in client.portal.call(race):
        _finished(result)


def test_the_write_up_closer_and_a_teammate_change_both_finish(
    client, stub_hooks, monkeypatch
):
    """With a message still waiting to be read in the task, the platform's
    write-up closer leaves the task open, and a teammate change made at that
    moment goes through: neither aborts the other."""
    _, task, _ = _task_under_way(client)
    monkeypatch.setattr(
        get_work_runner()._intake, "_schedule_message", lambda *a, **k: None
    )
    waiting = client.post(
        f"/topics/{task}/messages",
        json={
            "request_id": str(uuid.uuid4()),
            "content": "还有一件事",
            "expects_agent": True,
        },
        headers=session_auth_headers("alice"),
    )
    assert waiting.status_code == 200, waiting.text
    hold = _Hold()
    monkeypatch.setattr(TaskService, "give_agent", hold.around(TaskService.give_agent))

    async def race():
        change = asyncio.ensure_future(
            _patch(client, f"/topics/{task}/task", {"agent_handle": None})
        )
        await asyncio.wait_for(hold.reached.wait(), 10)
        close = asyncio.ensure_future(_close_after_summary(client, task))
        await asyncio.sleep(0.5)
        hold.release.set()
        return await asyncio.wait_for(
            asyncio.gather(change, close, return_exceptions=True), 20
        )

    changed, closed = client.portal.call(race)

    _finished(changed)
    _finished(closed)
    assert changed.status_code == 200, changed.text
    assert closed is None


def test_a_reply_in_a_thread_and_turning_that_thread_into_a_task_both_finish(
    client, stub_hooks, monkeypatch
):
    """Someone replies in a 支线 at the moment someone else turns that 支线
    into a task. A reply counts itself on the 支线 (trigger
    ``blocks_thread_replied``) after taking the 支线's numbering; turning it
    into a task is held once under way, the reply sent, then let go: both
    finish."""
    monkeypatch.setattr(
        get_work_runner()._intake, "_schedule_message", lambda *a, **k: None
    )
    _, _, room = _channel(client)
    root = post_message(client, room, "alice", {"content": "登录要不要加验证码？"})
    thread = client.post(
        f"/blocks/{root['id']}/thread", headers=session_auth_headers("alice")
    ).json()["data"]["id"]
    post_message(client, thread, "alice", {"content": "先说一句"})
    hold = _Hold()
    monkeypatch.setattr(
        thread_task, "thread_teammate", hold.around(thread_task.thread_teammate)
    )

    async def race():
        turn = asyncio.ensure_future(_post(client, f"/blocks/{root['id']}/upgrade", {}))
        await asyncio.wait_for(hold.reached.wait(), 10)
        reply = asyncio.ensure_future(
            _post(
                client,
                f"/topics/{thread}/messages",
                {"request_id": str(uuid.uuid4()), "content": "同时回一句"},
            )
        )
        await asyncio.sleep(0.5)
        hold.release.set()
        return await asyncio.wait_for(
            asyncio.gather(turn, reply, return_exceptions=True), 20
        )

    turned, replied = client.portal.call(race)

    _finished(turned)
    _finished(replied)
    assert turned.status_code == 200, turned.text
    assert replied.status_code == 200, replied.text


class _Sweep:
    async def stop_work(self, conversation):
        return None


def _two_rooms_tasks_crossing(client) -> tuple[str, str, uuid.UUID, uuid.UUID]:
    """A project with two rooms, each holding a task overdue for its write-up
    with a request still unread in it: the larger task id in the room created
    first, the smaller in the second. Archiving the project reaches them in
    room order; the overdue sweep, in id order. Returns the project, the first
    room, and the two task ids, smaller first."""
    project, _, first = _channel(client)
    second = client.post(
        "/topics",
        json={"project_id": project, "title": "第二个"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    ids = []
    for room in (first, second):
        made = client.post(
            f"/topics/{room}/tasks", json={}, headers=session_auth_headers("alice")
        )
        assert made.status_code == 200, made.text
        ids.append(uuid.UUID(made.json()["data"]["id"]))
    low, high = sorted(ids, key=str)

    async def overdue_and_crossing():
        # One task per transaction: one that changes several names them all
        # first (`lock_tasks_for_change`).
        for task_id, room in ((high, first), (low, second)):
            async with client.test_factory() as db:
                await db.execute(
                    update(Task)
                    .where(Task.id == task_id)
                    .values(
                        room_id=uuid.UUID(room),
                        closing_since=datetime.now(UTC) - timedelta(hours=1),
                    )
                )
                await db.commit()

    client.portal.call(overdue_and_crossing)
    for task_id in (high, low):
        sent = client.post(
            f"/topics/{task_id}/messages",
            json={
                "request_id": str(uuid.uuid4()),
                "content": "还没读",
                "expects_agent": True,
            },
            headers=session_auth_headers("alice"),
        )
        assert sent.status_code == 200, sent.text
    return project, first, low, high


@pytest.mark.parametrize(
    "archive_first", [True, False], ids=["archive-then-sweep", "sweep-then-archive"]
)
def test_archiving_a_project_and_the_overdue_sweep_both_finish(
    client, stub_hooks, monkeypatch, archive_first
):
    """Two transactions that each change several tasks, the same two, reaching
    them in opposite orders: archiving the project room by room, the overdue
    sweep task by task. Either one held part way, the other started, both
    finish."""
    monkeypatch.setattr(
        get_work_runner()._intake, "_schedule_message", lambda *a, **k: None
    )
    project, first_room, _, _ = _two_rooms_tasks_crossing(client)
    reached, release = asyncio.Event(), asyncio.Event()
    archive_one = TopicService._archive_one
    unfinished_work = closing.unfinished_work

    async def archive_held_after_its_first_room(self, topic, **kwargs):
        done = await archive_one(self, topic, **kwargs)
        if archive_first and str(topic.id) == first_room and not reached.is_set():
            reached.set()
            await release.wait()
        return done

    async def sweep_held_at_its_first_task(*args, **kwargs):
        if not archive_first and not reached.is_set():
            reached.set()
            await release.wait()
        return await unfinished_work(*args, **kwargs)

    monkeypatch.setattr(TopicService, "_archive_one", archive_held_after_its_first_room)
    monkeypatch.setattr(closing, "unfinished_work", sweep_held_at_its_first_task)

    async def sweep():
        async with client.test_factory() as db:
            swept = await closing.close_overdue(db, _Sweep())
            await db.commit()
            return swept

    def archive():
        return _post(client, f"/projects/{project}/archive", {})

    async def race():
        one = asyncio.ensure_future(archive() if archive_first else sweep())
        await asyncio.wait_for(reached.wait(), 10)
        two = asyncio.ensure_future(sweep() if archive_first else archive())
        await asyncio.sleep(0.5)
        release.set()
        return await asyncio.wait_for(
            asyncio.gather(one, two, return_exceptions=True), 20
        )

    for result in client.portal.call(race):
        _finished(result)


def test_a_task_is_not_locked_after_a_line_was_written_elsewhere(client, stub_hooks):
    """A line written into a conversation takes that conversation's numbering
    lock. A transaction that wrote into the room and then reaches for a task's
    lock takes the two in the order a close takes the other way round (the
    task, then the room), so it is refused before it waits."""
    _, _, room = _channel(client)
    task, _ = _task_with_a_turn(client, room)

    async def room_line_then_task():
        async with client.test_factory() as db:
            await platform_line(
                db,
                about=EventAbout.room,
                project_id=(await db.get(Task, uuid.UUID(task))).project_id,
                room_id=uuid.UUID(room),
                author="system",
                content="频道里先说一句",
            )
            try:
                await lock_task_for_change(db, uuid.UUID(task))
            except NumberingOutOfOrder:
                return "refused"
            finally:
                await db.rollback()
            return "locked"

    assert client.portal.call(room_line_then_task) == "refused"


def test_a_deployment_names_the_writer_that_asked_for_a_numbering_lock_late(
    client, stub_hooks, monkeypatch, caplog
):
    """Where an out-of-order ask only logs (a deployment), the log names the
    code that made it: the async function whose write asked, not only the
    database library that ran the flush."""
    monkeypatch.setattr(settings, "strict_numbering_order", False)
    project, _, one = _channel(client)
    other = client.post(
        "/topics",
        json={"project_id": project, "title": "第二个"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]

    async def a_writer_into_two_rooms_one_after_the_other():
        async with client.test_factory() as db:
            for room in (one, other):
                await BlockRepository(db).add(
                    project_id=uuid.UUID(project),
                    conversation_id=uuid.UUID(room),
                    author="system",
                    author_type=AuthorType.platform,
                    content="说一句",
                )
            await db.rollback()

    with caplog.at_level(logging.ERROR, logger="app.domain.conversation.numbering"):
        client.portal.call(a_writer_into_two_rooms_one_after_the_other)

    said = [
        record.getMessage()
        for record in caplog.records
        if record.name == "app.domain.conversation.numbering"
        and record.levelno == logging.ERROR
    ]
    assert len(said) == 1, said
    assert "a_writer_into_two_rooms_one_after_the_other" in said[0]
