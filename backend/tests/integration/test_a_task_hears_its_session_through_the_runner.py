"""A task's board entry follows what the seat's runner reports about its session.

Rules a person can state without the code:

- when the runner reports a task's session gone, the task says its teammate
  left, and what the teammate last said about its work stays as it said it;
- while the runner says a task's session is running and the teammate's report
  has not moved for longer than the threshold, the platform's own clock asks
  that session for a new report; a session that is not running is not asked.

Real PostgreSQL; the reports go through the platform's report path and the
reminder through the application's registered clocks.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import update

from app.core.config import settings
from app.domain.agent import runner_reports, session_state
from app.domain.agent.harness.driven import lifecycle
from app.domain.agent.reads import Report, SessionExited, SessionRunning
from app.domain.room_task.models import Task
from app.periodic_jobs import periodic_jobs
from tests.integration.conftest import (
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
    task_agent_headers,
)

HARNESS = "claude-code"


class _Chat:
    """What the report path and the reminder read of the room's service: the
    database, the live inputs, and the way into a running session, which here
    records what it was told. The other clocks' entry points are never called."""

    def __init__(self, client) -> None:
        self.session_factory = client.test_factory
        self.live = SimpleNamespace(unread_inputs={})
        self.told: list[tuple[uuid.UUID, str]] = []
        self.stop_work_in_closed_tasks = AsyncMock()
        self.compute = SimpleNamespace(working_conversations=set)
        self.sweep_memory_dreams = AsyncMock()

    async def notify_running_session(self, topic_id: uuid.UUID, notice: str) -> bool:
        self.told.append((topic_id, notice))
        return True


class _Runner:
    """The seat's runner: registered, numbering its events. ``written_under``
    is the epoch an event was written under, when it is older than the
    registration delivering it: a backlog the runner before this one left."""

    def __init__(self, client, task_id: str, seat: str) -> None:
        self.client = client
        self.key = session_state.SeatKey(uuid.UUID(task_id), seat, HARNESS)
        self.seq = 0
        self.register()

    def register(self) -> None:
        self.epoch = self.client.portal.call(
            session_state.register, self.client.test_factory, self.key
        )

    def reports(self, chat: _Chat, fact, *, written_under: int | None = None) -> None:
        self.seq += 1
        report = Report(
            self.key.conversation_id,
            self.key.agent_handle,
            HARNESS,
            self.epoch,
            self.epoch if written_under is None else written_under,
            self.seq,
            datetime.now(UTC),
            fact,
        )
        assert self.client.portal.call(runner_reports.apply, chat, report) is not False


def _a_task(client) -> tuple[str, str, str]:
    project = post_project(client, {"name": "Lifecycle"}, owner="alice").json()["data"]
    room = project["root_topic_id"]
    seat = room_agent_seat(client, room)
    task = open_task(client, room, "跑一遍迁移", owner="alice")
    return project["id"], task["id"], seat


def _task(client, task_id: str) -> dict:
    r = client.get(f"/topics/{task_id}/task", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_the_runner_reporting_the_session_gone_marks_the_teammate_left(client):
    project_id, task_id, seat = _a_task(client)
    said = client.put(
        f"/topics/{task_id}/task/progress",
        json={"state": "waiting", "note": "等构建"},
        headers=task_agent_headers(project_id, task_id, seat),
    )
    assert said.status_code == 200, said.text
    chat = _Chat(client)
    runner = _Runner(client, task_id, seat)
    runner.reports(chat, SessionRunning(uuid.uuid4()))
    assert _task(client, task_id)["agent_left_at"] is None

    runner.reports(chat, SessionExited("launch-1"))

    task = _task(client, task_id)
    assert task["agent_left_at"] is not None
    assert task["progress_state"] == "waiting"
    assert task["progress_note"] == "等构建"
    assert task["progress_set_by"] == seat
    assert task["presentation"]["phrase"] == "waiting"


@pytest.mark.parametrize(
    "registered",
    [
        # The process before was registered under the epoch the new one
        # replaced: its exit, reported by its own runner from the backlog or
        # by the next runner for it, carries that epoch.
        True,
        # It died before it was ever registered.
        False,
    ],
)
def test_an_earlier_process_ending_leaves_the_running_session_be(client, registered):
    """The seat was registered again and its new session is at work when the
    exit of the process before it arrives: the task does not say its teammate
    left, and the seat still reads as running."""
    _project_id, task_id, seat = _a_task(client)
    chat = _Chat(client)
    runner = _Runner(client, task_id, seat)
    runner.reports(chat, SessionRunning(uuid.uuid4()))
    earlier = runner.epoch
    runner.register()
    runner.reports(chat, SessionRunning(uuid.uuid4()))

    runner.reports(
        chat,
        SessionExited("launch-1"),
        written_under=earlier if registered else lifecycle.NEVER_REGISTERED,
    )

    assert _task(client, task_id)["agent_left_at"] is None

    async def running() -> set[uuid.UUID]:
        async with client.test_factory() as session:
            return await session_state.running_conversations(session)

    assert uuid.UUID(task_id) in client.portal.call(running)


def test_a_session_running_again_takes_the_left_mark_away(client):
    """The task's session ended, its seat was registered again and the new
    session started working: 「已离开」 goes, and what the teammate said stays."""
    project_id, task_id, seat = _a_task(client)
    said = client.put(
        f"/topics/{task_id}/task/progress",
        json={"state": "waiting", "note": "等构建"},
        headers=task_agent_headers(project_id, task_id, seat),
    )
    assert said.status_code == 200, said.text
    chat = _Chat(client)
    runner = _Runner(client, task_id, seat)
    runner.reports(chat, SessionRunning(uuid.uuid4()))
    runner.reports(chat, SessionExited("launch-1"))
    assert _task(client, task_id)["agent_left_at"] is not None

    runner.register()
    runner.reports(chat, SessionRunning(uuid.uuid4()))

    task = _task(client, task_id)
    assert task["agent_left_at"] is None
    assert (task["progress_state"], task["progress_note"]) == ("waiting", "等构建")


def test_another_agents_session_ending_does_not_mark_the_teammate_left(client):
    """A task's conversation may hold another agent's session as well (it was
    asked in the task, or works beside the teammate). That session ending is
    not the teammate leaving: the task keeps its words unnamed, because the
    mark would be shown beside them as the teammate's own."""
    project_id, task_id, seat = _a_task(client)
    said = client.put(
        f"/topics/{task_id}/task/progress",
        json={"state": "working", "note": "在改页头"},
        headers=task_agent_headers(project_id, task_id, seat),
    )
    assert said.status_code == 200, said.text
    chat = _Chat(client)
    beside = _Runner(client, task_id, "cheese-beside")
    beside.reports(chat, SessionRunning(uuid.uuid4()))
    beside.reports(chat, SessionExited("launch-beside"))

    task = _task(client, task_id)
    assert task["agent_left_at"] is None
    assert (task["progress_state"], task["progress_set_by"]) == ("working", seat)


def test_another_agents_session_running_does_not_take_the_mark_away(client):
    """The teammate said where it stands and then ended; another agent's
    session started working in the same task. The teammate is still gone, so
    「已离开」 stays beside what it said."""
    project_id, task_id, seat = _a_task(client)
    said = client.put(
        f"/topics/{task_id}/task/progress",
        json={"state": "working", "note": "在改页头"},
        headers=task_agent_headers(project_id, task_id, seat),
    )
    assert said.status_code == 200, said.text
    chat = _Chat(client)
    runner = _Runner(client, task_id, seat)
    runner.reports(chat, SessionRunning(uuid.uuid4()))
    runner.reports(chat, SessionExited("launch-1"))
    assert _task(client, task_id)["agent_left_at"] is not None

    beside = _Runner(client, task_id, "cheese-beside")
    beside.reports(chat, SessionRunning(uuid.uuid4()))

    assert _task(client, task_id)["agent_left_at"] is not None


def _reported_long_ago(client, task_id: str) -> None:
    long_ago = datetime.now(UTC) - timedelta(
        seconds=settings.chat_progress_reminder_after_s + 60
    )

    async def stamp() -> None:
        async with client.test_factory() as session:
            await session.execute(
                update(Task)
                .where(Task.id == uuid.UUID(task_id))
                .values(progress_state="working", progress_set_at=long_ago)
            )
            await session.commit()

    client.portal.call(stamp)


def test_the_clock_asks_a_running_session_whose_report_went_stale(client):
    _project_id, task_id, seat = _a_task(client)
    _reported_long_ago(client, task_id)
    chat = _Chat(client)
    clocks = {
        job.name: job
        for job in periodic_jobs(
            chat=chat,  # type: ignore[arg-type]  # only what these clocks read
            machines=AsyncMock(),
            sandboxes=AsyncMock(),
            compute=AsyncMock(),
            sessions=client.test_factory,
            get_runner=Mock(),
        )
    }
    reminder = clocks["task state reminder"]
    runner = _Runner(client, task_id, seat)

    # Registered, not running: however old the report, nobody is asked.
    client.portal.call(reminder._job)
    assert chat.told == []

    runner.reports(chat, SessionRunning(uuid.uuid4()))
    client.portal.call(reminder._job)

    assert [topic for topic, _ in chat.told] == [uuid.UUID(task_id)]
    assert "cheese_task_state" in chat.told[0][1]
