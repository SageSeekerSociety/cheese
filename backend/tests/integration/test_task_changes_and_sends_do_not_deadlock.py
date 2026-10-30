"""Changes to a task, and messages sent in it, never abort each other.

A message sent in a task, and every change to the task — closing it, the
platform closing it after its write-up, giving it to another teammate, handing
it to another owner, changing who works it, archiving its project — each runs
in one transaction. Run at the same moment, any two of them finish: one waits
for the other, then goes on (or is refused for what it now reads, as a 4xx).
None of them fails because the two waited on each other.

Each case starts one operation, holds it at the point where it is under way in
the task, starts the other and lets it go as far as it can, then lets the
first one finish.
"""

import asyncio
import itertools
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import update

from app.api.deps import get_work_runner
from app.api.routes import topics_tasks
from app.domain.agent.turn.intake import human
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.room_task import closing, heard
from app.domain.room_task.models import Task
from app.domain.room_task.services import TaskService
from app.domain.topic.services import TopicService
from tests.integration.conftest import join_project_team, session_auth_headers
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
