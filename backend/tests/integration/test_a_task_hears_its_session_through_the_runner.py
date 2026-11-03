"""A task's board entry follows what the seat's runner reports about its session.

Rules a person can state without the code:

- while the runner says the task's own teammate's session is running, the
  task is 在做 unless the teammate reported in that same run, as the runner
  names the run to the report's call: a teammate that never said anything, or
  said 「在等」 in an earlier run, is at work; what it says in this run stands,
  however late the runner's events reach the platform; a report naming no run
  does not speak for a running session; another agent's session in the task
  counts for nothing;
- a page showing the task hears when its session starts or stops running;
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
from app.domain.agent.reads import Report, SessionExited, SessionIdle, SessionRunning
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent.service import AgentResult
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

    def __init__(self, client, task_id: str, seat: str, harness: str = HARNESS) -> None:
        self.client = client
        self.key = session_state.SeatKey(uuid.UUID(task_id), seat, harness)
        self.seq = 0
        self.register()

    def register(self) -> None:
        self.epoch = self.client.portal.call(
            session_state.register, self.client.test_factory, self.key
        )

    def upcoming(self, events: int = 1) -> str:
        """The run as the runner names it to a platform call (`X-Cheese-Run`)
        when the run begins with the ``events``-th event from now."""
        return f"{self.key.harness}/{self.epoch}/{self.seq + events}"

    def running(self, chat: _Chat) -> str:
        """The session starts a run; what the runner names that run."""
        run = self.upcoming()
        self.reports(chat, SessionRunning(uuid.uuid4()))
        return run

    def idle(self, chat: _Chat) -> None:
        """The same process goes idle."""
        self.reports(chat, SessionIdle(None, AgentResult(text="", session_id=None)))

    def reports(self, chat: _Chat, fact, *, written_under: int | None = None) -> None:
        self.seq += 1
        report = Report(
            self.key.conversation_id,
            self.key.agent_handle,
            self.key.harness,
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


def _phrases(client, project_id: str, task_id: str) -> set[str]:
    """What the task reads as on its own page, in the project's task list and
    in its channel's task list: one answer, or the set shows the disagreement."""
    headers = session_auth_headers("alice")
    page = _task(client, task_id)
    listed = client.get(f"/projects/{project_id}/tasks", headers=headers)
    assert listed.status_code == 200, listed.text
    in_room = client.get(
        f"/topics/{page['room_id']}/tasks", params={"limit": 0}, headers=headers
    )
    assert in_room.status_code == 200, in_room.text
    rows = [
        *(r for r in listed.json()["data"]["data"] if r["id"] == task_id),
        *(r for r in in_room.json()["data"]["data"] if r["id"] == task_id),
    ]
    assert len(rows) == 2
    return {
        page["presentation"]["phrase"],
        *(r["presentation"]["phrase"] for r in rows),
    }


def _say(
    client, project_id: str, task_id: str, seat: str, state: str, run: str | None = None
) -> None:
    """The seat reports ``state``; ``run`` is the run its call names, as the
    session's runner told it (none: a runner that names no runs)."""
    headers = task_agent_headers(project_id, task_id, seat)
    if run is not None:
        headers = {**headers, "X-Cheese-Run": run}
    said = client.put(
        f"/topics/{task_id}/task/progress", json={"state": state}, headers=headers
    )
    assert said.status_code == 200, said.text


def test_a_running_session_is_at_work_before_its_teammate_says_anything(client):
    """The teammate has not reported yet, but its session is running: the task
    is 在做 everywhere it is shown, not 开启. When the session ends it is back
    to 开启 — nothing was said, and nothing runs."""
    project_id, task_id, seat = _a_task(client)
    chat = _Chat(client)
    runner = _Runner(client, task_id, seat)
    assert _phrases(client, project_id, task_id) == {"started"}

    runner.reports(chat, SessionRunning(uuid.uuid4()))
    assert _phrases(client, project_id, task_id) == {"running"}

    runner.reports(chat, SessionExited("launch-1"))
    assert _phrases(client, project_id, task_id) == {"started"}


def test_a_session_woken_after_its_teammate_said_waiting_is_at_work(client):
    """The teammate said 「在等」 and its session went quiet; then the session
    ran again (the build it waited on finished and woke it). It is doing
    something now, so the task is 在做; once that run ends, what the teammate
    last said is the last word again."""
    project_id, task_id, seat = _a_task(client)
    chat = _Chat(client)
    runner = _Runner(client, task_id, seat)
    first = runner.running(chat)
    _say(client, project_id, task_id, seat, "waiting", first)
    runner.idle(chat)
    assert _phrases(client, project_id, task_id) == {"waiting"}

    runner.running(chat)
    assert _phrases(client, project_id, task_id) == {"running"}

    runner.idle(chat)
    assert _phrases(client, project_id, task_id) == {"waiting"}


def test_a_report_made_before_its_runs_start_is_applied_still_stands(client):
    """The teammate's session started working and said 「卡住」 before the
    platform had applied the runner's report that the session was running (the
    runner's events reached it late). The report was made in that run, so the
    task reads 卡住 once the late report lands, not 在做."""
    project_id, task_id, seat = _a_task(client)
    chat = _Chat(client)
    runner = _Runner(client, task_id, seat)
    runner.running(chat)
    runner.idle(chat)

    _say(client, project_id, task_id, seat, "blocked", runner.upcoming())
    runner.running(chat)

    assert _phrases(client, project_id, task_id) == {"blocked"}


def test_a_report_made_before_both_the_last_idle_and_its_run_are_applied_stands(
    client,
):
    """The runner's events are late twice over: the platform still has the
    previous run going when the teammate, already in the next run, says
    「卡住」. Once both land, the report is that next run's: 卡住."""
    project_id, task_id, seat = _a_task(client)
    chat = _Chat(client)
    runner = _Runner(client, task_id, seat)
    runner.running(chat)

    _say(client, project_id, task_id, seat, "blocked", runner.upcoming(2))
    runner.idle(chat)
    runner.running(chat)

    assert _phrases(client, project_id, task_id) == {"blocked"}


def test_a_process_replaced_mid_run_starts_a_new_run(client):
    """The teammate said 「在等」, and its process was replaced while the seat
    still read running (the old process's exit never landed). The new process
    working is a run the report was not made in: 在做."""
    project_id, task_id, seat = _a_task(client)
    chat = _Chat(client)
    runner = _Runner(client, task_id, seat)
    first = runner.running(chat)
    _say(client, project_id, task_id, seat, "waiting", first)
    assert _phrases(client, project_id, task_id) == {"waiting"}

    runner.register()
    runner.running(chat)

    assert _phrases(client, project_id, task_id) == {"running"}


def test_a_report_made_while_the_session_is_idle_is_its_last_runs(client):
    """A background task of the teammate's says 「卡住」 after the session went
    idle: it speaks for the run that just ended, so it stands while nothing
    runs, and the next run is one it was not made in: 在做."""
    project_id, task_id, seat = _a_task(client)
    chat = _Chat(client)
    runner = _Runner(client, task_id, seat)
    first = runner.running(chat)
    runner.idle(chat)

    _say(client, project_id, task_id, seat, "blocked", first)
    assert _phrases(client, project_id, task_id) == {"blocked"}

    runner.running(chat)
    assert _phrases(client, project_id, task_id) == {"running"}


def test_a_report_naming_no_run_does_not_speak_for_a_running_session(client):
    """A runner of an earlier release names no run to the calls its session
    makes. While its session runs, a report from it is not known to be that
    run's: the task reads 在做 until the session stops."""
    project_id, task_id, seat = _a_task(client)
    chat = _Chat(client)
    runner = _Runner(client, task_id, seat)
    runner.running(chat)

    _say(client, project_id, task_id, seat, "blocked")
    assert _phrases(client, project_id, task_id) == {"running"}

    runner.idle(chat)
    assert _phrases(client, project_id, task_id) == {"blocked"}


def test_a_report_naming_its_run_in_another_shape_names_none(client):
    """A run named in a shape the platform does not read (here a harness wider
    than any) is taken as naming no run: the report is kept, and does not speak
    for the running session."""
    project_id, task_id, seat = _a_task(client)
    chat = _Chat(client)
    runner = _Runner(client, task_id, seat)
    runner.running(chat)

    _say(client, project_id, task_id, seat, "blocked", "x" * 40 + "/1/1")
    assert _phrases(client, project_id, task_id) == {"running"}


def test_another_agents_session_running_does_not_make_the_task_in_progress(client):
    """Another agent works in the task's conversation (it was asked in, it
    reviews). That is not the task being done: with no report the task stays
    开启, and a teammate's 「在等」 stays 在等."""
    project_id, task_id, seat = _a_task(client)
    chat = _Chat(client)
    beside = _Runner(client, task_id, "cheese-beside")
    beside.running(chat)
    assert _phrases(client, project_id, task_id) == {"started"}

    _say(client, project_id, task_id, seat, "waiting")
    assert _phrases(client, project_id, task_id) == {"waiting"}


def test_what_the_teammate_says_while_its_session_runs_stands(client):
    """The teammate reports from inside the run, knowing the run is going on:
    「卡住」 and 「在等」 said during it are what the task reads as."""
    project_id, task_id, seat = _a_task(client)
    chat = _Chat(client)
    runner = _Runner(client, task_id, seat)
    run = runner.running(chat)

    _say(client, project_id, task_id, seat, "blocked", run)
    assert _phrases(client, project_id, task_id) == {"blocked"}

    _say(client, project_id, task_id, seat, "waiting", run)
    assert _phrases(client, project_id, task_id) == {"waiting"}


def test_a_backend_reattaching_a_running_session_keeps_its_run(client):
    """A backend came up and re-registered the teammate's runner, which was
    still in the run it said 「卡住」 in. The run goes on, and so does 卡住."""
    project_id, task_id, seat = _a_task(client)
    chat = _Chat(client)
    runner = _Runner(client, task_id, seat)
    run = runner.running(chat)
    _say(client, project_id, task_id, seat, "blocked", run)
    assert _phrases(client, project_id, task_id) == {"blocked"}

    runner.register()

    assert _phrases(client, project_id, task_id) == {"blocked"}


def test_a_run_a_backend_reads_from_the_backlog_after_reattaching_is_the_run(
    client,
):
    """The session woke while no backend was reading: its runner wrote that the
    run began, and the teammate said 「卡住」 in it. A backend came up and
    re-registered the runner before reading the backlog, so the event arrives
    under the epoch before. It is still the run going on: 卡住."""
    project_id, task_id, seat = _a_task(client)
    chat = _Chat(client)
    runner = _Runner(client, task_id, seat)
    runner.running(chat)
    runner.idle(chat)
    written_under = runner.epoch
    run = runner.upcoming()

    _say(client, project_id, task_id, seat, "blocked", run)
    runner.register()
    runner.reports(chat, SessionRunning(uuid.uuid4()), written_under=written_under)

    assert _phrases(client, project_id, task_id) == {"blocked"}


def test_a_session_woken_while_no_backend_read_it_is_at_work(client):
    """The teammate said 「在等」 and went idle; its session woke while no
    backend was reading, and a backend came up and re-registered the runner
    before reading that the run began. The session is in a run the report was
    not made in: 在做."""
    project_id, task_id, seat = _a_task(client)
    chat = _Chat(client)
    runner = _Runner(client, task_id, seat)
    first = runner.running(chat)
    _say(client, project_id, task_id, seat, "waiting", first)
    runner.idle(chat)
    written_under = runner.epoch

    runner.register()
    runner.reports(chat, SessionRunning(uuid.uuid4()), written_under=written_under)

    assert _phrases(client, project_id, task_id) == {"running"}


def test_a_report_by_the_teammate_before_does_not_speak_for_the_one_now(client):
    """The teammate working the task said 「在等」 in its first run; then the
    task was given to another teammate, whose first run is under way and is
    named exactly as the first one's was. The name of a run alone does not say
    whose run it is: the words are the previous teammate's, and the task is
    在做."""
    project_id, task_id, seat = _a_task(client)
    chat = _Chat(client)
    first = _Runner(client, task_id, seat)
    said_in = first.running(chat)
    _say(client, project_id, task_id, seat, "waiting", said_in)
    first.reports(chat, SessionExited("launch-1"))

    made = client.post(f"/projects/{project_id}/agents", json={"handle": "pai"})
    assert made.status_code == 200, made.text
    second_seat = made.json()["data"]["seat_handle"]
    given = client.patch(
        f"/topics/{task_id}/task",
        json={"agent_handle": second_seat},
        headers=session_auth_headers("alice"),
    )
    assert given.status_code == 200, given.text
    second = _Runner(client, task_id, second_seat)
    assert second.running(chat) == said_in

    assert _phrases(client, project_id, task_id) == {"running"}


@pytest.fixture
def frames(monkeypatch) -> list[tuple[str, dict]]:
    """Every frame published to any channel, in order."""
    seen: list[tuple[str, dict]] = []
    broker = get_broker()
    original = broker.publish

    async def publish(channel, frame):
        seen.append((channel, frame))
        await original(channel, frame)

    monkeypatch.setattr(broker, "publish", publish)
    return seen


def test_a_tasks_pages_hear_its_session_start_and_stop(client, frames):
    """Whether the session runs is part of what the task reads as, so its
    channel and its own page are told to read it again when the runner says
    the session started, and when it says the session ended."""
    _project_id, task_id, seat = _a_task(client)
    room = _task(client, task_id)["room_id"]
    chat = _Chat(client)
    runner = _Runner(client, task_id, seat)
    told = {"type": "state", "resource": "topics", "id": room}

    frames.clear()
    runner.reports(chat, SessionRunning(uuid.uuid4()))
    assert {channel for channel, frame in frames if frame == told} == {room, task_id}

    frames.clear()
    runner.reports(chat, SessionExited("launch-1"))
    assert {channel for channel, frame in frames if frame == told} == {room, task_id}


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
    assert "cheese_task(action=state)" in chat.told[0][1]
