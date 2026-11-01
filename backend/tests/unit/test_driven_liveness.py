"""How work is ended when the session will not end it, and what its ending
settles.

Every case runs the real room sessions, session core, Claude Code runner and
its lifecycle reports against a scripted session (``StubChannel``); only the
clocks are shortened. What the room receives is what its reader receives. The
runner keeps the stuck-work clocks: the room keeps none for Claude Code.
"""

import asyncio
import time
import uuid
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.harness.claude_code import runner as claude_runner
from app.domain.agent.harness.claude_code import stuck as claude_stuck
from app.domain.agent.platform_failures import (
    PROMPT_UNDELIVERED_CODE,
    TURN_TIMEOUT_CODE,
)
from app.domain.agent.reads import (
    InputDropped,
    InputRead,
    Report,
    SessionExited,
    SessionIdle,
    SessionRunning,
)
from app.domain.agent.service import AgentResult
from app.domain.agent.session_host import host as session_host
from app.domain.delivery.input_identity import InputIdentity
from tests.conftest import StubChannel, drain_hooks
from tests.support.room_reader import room_reader

_REAL_SLEEP = asyncio.sleep


class Scripted(StubChannel):
    """A session that does, for the input that opens the turn, only what the
    test scripts — and reads every later input without answering it."""

    def __init__(self, script, **policy: float) -> None:
        super().__init__(**policy)
        self.script = script
        self.opened = False

    def emit_turn(
        self,
        topic_id: uuid.UUID,
        prompt: str,
        reply: str,
        *,
        agent: str | None = None,
    ) -> None:
        if not self.opened:
            self.opened = True
            self.script(self, topic_id, prompt)

    def begins(self, topic_id: uuid.UUID, prompt: str) -> None:
        """The build takes the input — without echoing it back yet."""
        session = self._session_for(topic_id)
        identifier = next(
            message["uuid"]
            for message in reversed(session.written)
            if message.get("type") == "user" and message["message"]["content"] == prompt
        )
        self.record(
            topic_id,
            type="command_lifecycle",
            command_uuid=identifier,
            state="started",
        )


class Room:
    """One room's session, and everything the runtime told the room about it."""

    def __init__(self, channel: Scripted) -> None:
        self.channel = channel
        self.runtime = channel.runtime
        self.session = SessionRef(
            uuid.uuid4(), uuid.uuid4(), "cheese", harness=CLAUDE_CODE
        )
        self.topic = self.session.topic_id
        self.work = uuid.uuid4()
        self.events: list[tuple[uuid.UUID, object]] = []
        self.receipts: list[str] = []
        self.unread: dict[str, float] = {}
        self.inputs: dict[uuid.UUID, str] = {}
        self.identities: dict[uuid.UUID, InputIdentity] = {}
        #: Every event the runner reported, as the room's reader got it.
        self.reports: list[Report] = []

        async def consume(_project, _topic, work, event, _eid, _seen, _unsolicited):
            self.events.append((work, event))

        async def report(evidence: Report):
            self.reports.append(evidence)
            fact = evidence.fact
            if isinstance(fact, InputRead):
                assert (
                    fact.receipt.identity
                    == self.identities[fact.receipt.identity.input_id]
                )
                text = self.inputs[fact.receipt.identity.input_id]
                self.receipts.append(text)
                self.unread.pop(text, None)

        def oldest_unread(_topic):
            return min(self.unread.values(), default=None)

        self.runtime.report_to(
            room_reader(events=consume, reports=report),
            unread=oldest_unread,
            memory=AsyncMock(),
        )

    def facts(self, kind) -> list:
        return [r.fact for r in self.reports if isinstance(r.fact, kind)]

    def results(self) -> list[AgentResult]:
        return [event for _, event in self.events if isinstance(event, AgentResult)]

    def register_input(self, text: str) -> AsyncMock:
        async def register(identity: InputIdentity) -> None:
            self.inputs[identity.input_id] = text
            self.identities[identity.input_id] = identity

        return AsyncMock(side_effect=register)

    async def send(self, text: str) -> None:
        self.unread[text] = time.monotonic()
        await self.runtime.send(
            self.session,
            text,
            system_prompt="",
            work_id=self.work,
            on_mark=lambda _: None,
            register_input=self.register_input(text),
        )

    async def steer(self, text: str) -> None:
        self.unread[text] = time.monotonic()
        assert await self.runtime.steer(
            self.topic, text, register_input=self.register_input(text)
        )

    async def close(self) -> None:
        for seat in list(self.runtime.tasks):
            if seat[0] == self.topic:
                await self.runtime._detach(seat)


async def _until(check, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "the condition never came true"
        await _REAL_SLEEP(0.01)


@pytest.fixture
def quick_retries(monkeypatch):
    """The reading's two-second wait between failed reads, cut short."""

    class Quick:
        def __getattr__(self, name):
            return getattr(asyncio, name)

        async def sleep(self, seconds):
            await _REAL_SLEEP(min(seconds, 0.02))

    monkeypatch.setattr(session_host, "asyncio", Quick())


@pytest.fixture
def clocks(monkeypatch):
    """The runner's stuck-work clocks, as the platform registers it with them,
    and how often it looks, shortened."""
    monkeypatch.setattr(claude_runner, "WATCH_S", 0.05)

    def set_clocks(
        *, no_progress_s: float = 0.0, unread_s: float = 0.0, silent_s: float = 0.0
    ) -> None:
        monkeypatch.setattr(settings, "agent_no_progress_s", no_progress_s)
        monkeypatch.setattr(settings, "agent_unread_grace_s", unread_s)
        monkeypatch.setattr(settings, "agent_stuck_silence_s", silent_s)

    set_clocks()
    return set_clocks


def _talks(channel: Scripted, topic: uuid.UUID, prompt: str) -> None:
    channel.starts(topic)
    channel.acknowledges(topic, prompt)
    channel.says(topic, "still thinking about it")


async def test_the_runner_ends_work_that_talks_without_working_once(clocks):
    """A session that keeps saying things and never calls a tool or ends is in a
    loop. Its runner ends the work with the timeout, exactly once — the room
    keeps no clock of its own for it — and what the session says afterwards
    is not a second ending."""
    clocks(no_progress_s=0.3)
    room = Room(Scripted(_talks))
    try:
        await room.send("fix the login page")
        await _until(lambda: room.results())

        (ended,) = room.results()
        assert ended.is_error and ended.failure_code == TURN_TIMEOUT_CODE
        (idle,) = room.facts(SessionIdle)
        assert idle.failure == "stuck"
        # It was interrupted on the machine, not only told off here.
        session = room.channel._session_for(room.topic)
        assert any(
            message.get("request", {}).get("subtype") == "interrupt"
            for message in session.written
        )

        room.channel.stops(room.topic, "late news")
        await drain_hooks(room.channel, room.topic)
        assert room.results() == [ended]
    finally:
        await room.close()


def _takes_it_without_echo(channel: Scripted, topic: uuid.UUID, prompt: str) -> None:
    channel.starts(topic)
    channel.begins(topic, prompt)
    channel.says(topic, "working on something else")


def _deaf_to_the_interrupt(room: "Room") -> None:
    """The session takes the interrupt and never ends the work itself: its
    ``result`` records are dropped, as they are for a build that stopped
    answering."""
    for _key, runner in room.channel.sessions.items():
        observed = runner.observe

        def observe(record, _observed=observed):
            if record.get("type") != "result":
                return _observed(record)

        runner.observe = observe


def _says_nothing_at_all(channel: Scripted, topic: uuid.UUID, prompt: str) -> None:
    """The build takes the work, reads it, and then produces nothing — no
    words, no tool."""
    channel.begins(topic, prompt)
    channel.acknowledges(topic, prompt)


async def test_a_session_that_says_nothing_at_all_is_ended_by_its_runner(
    clocks, monkeypatch
):
    """The other two clocks both need something to go on: words said recently,
    or an input still waiting to be read. A session whose process is alive and
    whose build simply stopped answering has neither, so nothing would ever
    report that its work ended — and everything that reads the work as still
    running (the room's 「正在工作」, the task's state) keeps counting. The
    runner reports the ending itself and stops the process."""
    clocks(silent_s=0.3)
    # How long the session gets to end it itself, after being interrupted.
    monkeypatch.setattr(claude_stuck, "STUCK_GRACE_S", 0.1)
    room = Room(Scripted(_says_nothing_at_all))
    try:
        await room.send("fix the login page")
        # This one will not answer: the interrupt changes nothing, and no
        # result ever follows it. Only the runner can report the ending.
        _deaf_to_the_interrupt(room)
        await _until(lambda: room.results())

        (ended,) = room.results()
        # The work ended as failed, and the room was told which clock saw it.
        assert ended.is_error
        (idle,) = room.facts(SessionIdle)
        assert idle.failure == "stuck"
    finally:
        await room.close()


async def test_the_runner_ends_work_whose_input_nobody_read(clocks):
    clocks(unread_s=0.3)
    room = Room(Scripted(_takes_it_without_echo))
    try:
        await room.send("fix the login page")
        await _until(lambda: room.results())

        (ended,) = room.results()
        assert ended.is_error and ended.failure_code == PROMPT_UNDELIVERED_CODE
        assert room.receipts == []
    finally:
        await room.close()


def _runs_a_tool(channel: Scripted, topic: uuid.UUID, prompt: str) -> None:
    channel.starts(topic)
    channel.acknowledges(topic, prompt)
    channel.uses(topic, "Bash", command="pytest -q")


async def test_an_input_waiting_behind_a_running_tool_gets_its_grace_from_the_return(
    clocks,
):
    """Input is read at tool boundaries. Words said while a long command runs
    cannot be read before it returns, so waiting then is not a failure to
    read — the grace starts when the tool comes back."""
    grace = 0.3
    clocks(unread_s=grace)
    room = Room(Scripted(_runs_a_tool))
    try:
        await room.send("run the tests")
        await _until(lambda: room.receipts == ["run the tests"])
        await room.steer("and the linter too")

        # Several looks go by with the steer unread past its grace.
        await _REAL_SLEEP(1.0)
        assert room.results() == []

        returned_at = time.monotonic()
        room.channel.returns(room.topic, "Bash", "42 passed")
        await _until(lambda: room.results())

        (ended,) = room.results()
        assert ended.failure_code == PROMPT_UNDELIVERED_CODE
        assert time.monotonic() - returned_at >= grace
    finally:
        await room.close()


async def test_a_session_exit_ends_its_work_and_settles_what_it_never_read():
    """The runner says the process is gone: the work it was in the middle of
    failed, an input it had written and not read yet will never be read (so it
    may be offered again), and the reading is over."""
    room = Room(Scripted(_runs_a_tool))
    try:
        await room.send("run the tests")
        await _until(lambda: room.receipts == ["run the tests"])
        await room.steer("and the linter too")
        room.channel.alive = False
        await _until(lambda: room.results() and room.facts(SessionExited))

        (ended,) = room.results()
        assert ended.is_error
        assert ended.text == "Claude Code session process exited"
        (dropped,) = room.facts(InputDropped)
        assert room.inputs[dropped.identity.input_id] == "and the linter too"
        assert dropped.reason == "exited"
        (idle,) = room.facts(SessionIdle)
        # The failure is attached to what the work had read, and only that.
        assert [room.inputs[i] for i in idle.read_inputs] == ["run the tests"]
        # The reading is over with it.
        await _until(lambda: (room.topic, "cheese") not in room.runtime.live)
    finally:
        await room.close()


async def test_a_session_whose_process_never_started_says_why():
    """A relaunch whose process ended before Claude Code ever ran (dev,
    2026-10-12: its machine was being prepared again when the relaunch took the
    lease) is a session that did not start. The room hears the reason its
    runner recorded, as it does for a start that failed outright, not that a
    process exited."""
    channel = Scripted(_talks)
    channel.ending = (
        "Claude Code exited with status 1 before it started:\n"
        "Traceback (most recent call last):\n"
        '  File "/h/.cheese/remote-execution/client.py", line 622, '
        "in _take_leased_machine\n"
        "    client.acquire(deadline=time.monotonic())\n"
        '  File "/h/.cheese/remote-execution/executor_transport.py", line 739, '
        "in acquire\n"
        "RuntimeError: 环境仍在准备，对话和平台工具仍可用（等到操作时限仍未就绪）"
    )
    room = Room(channel)
    try:
        await room.send("fix the login page")
        await _until(lambda: room.channel._session_for(room.topic).working)
        room.channel.alive = False
        await _until(lambda: room.results())

        (ended,) = room.results()
        assert ended.is_error
        assert "process exited" not in str(ended.text)
        assert "环境还在准备" in str(ended.text)
        # What the runner recorded stays with the failure, for 现场.
        assert "_take_leased_machine" in (ended.log or "")
    finally:
        await room.close()


@pytest.mark.usefixtures("quick_retries")
async def test_an_unreachable_runner_ends_nothing_and_settles_nothing(monkeypatch):
    """A runner that stops answering is unreachable — which is what a machine
    still coming up looks like too. The room is told it is waiting; no work is
    ended and no input settled on it, because a runner that answers again
    reports what happened meanwhile."""
    monkeypatch.setattr(session_host, "LEASE_S", 0.3)
    room = Room(Scripted(_talks))
    told: list[bool] = []

    async def reachability(_project, _topic, _work, reachable, _reason):
        told.append(reachable)

    room.runtime.reader = room_reader(
        events=lambda *a: room.events.append((a[2], a[3])) or _REAL_SLEEP(0),
        reachability=reachability,
        reports=lambda r: room.reports.append(r) or _REAL_SLEEP(0),
    )
    try:
        await room.send("fix the login page")
        await _until(lambda: room.facts(SessionRunning))
        room.channel.drop_session(room.topic)
        await _until(lambda: told == [False])
        await _REAL_SLEEP(0.5)

        assert room.results() == []
        assert room.facts(InputDropped) == []
        assert room.facts(SessionExited) == []
        assert (room.topic, "cheese") in room.runtime.live
    finally:
        await room.close()


@pytest.mark.usefixtures("quick_retries")
async def test_a_runner_back_within_the_lease_keeps_its_work(monkeypatch):
    monkeypatch.setattr(session_host, "LEASE_S", 0.5)
    room = Room(Scripted(_talks))
    try:
        await room.send("fix the login page")
        await _until(lambda: room.facts(SessionRunning))
        session = room.channel.drop_session(room.topic)
        await _REAL_SLEEP(0.2)
        room.channel.sessions[(room.topic, session.actor)] = session
        await _REAL_SLEEP(0.6)
        assert room.results() == []

        room.channel.stops(room.topic, "done")
        await _until(lambda: room.results())
        (ended,) = room.results()
        assert not ended.is_error and ended.text == "done"
    finally:
        await room.close()


async def test_an_input_is_read_when_its_echo_comes_back_not_when_it_is_taken():
    """Claude Code says it read an input by echoing it (`isReplay`). The build
    taking it, or the runner writing it to stdin, is not that — for words said
    mid-work the echo comes at the next tool boundary, and only then is the
    message the room showed as waiting actually read."""
    room = Room(Scripted(_takes_it_without_echo))
    try:
        await room.send("fix the login page")
        await _until(lambda: room.channel._session_for(room.topic).working)
        await drain_hooks(room.channel, room.topic)
        assert room.receipts == []

        room.channel.acknowledges(room.topic, "fix the login page")
        await _until(lambda: room.receipts == ["fix the login page"])

        await room.steer("use the new theme")
        await _REAL_SLEEP(0.3)
        await drain_hooks(room.channel, room.topic)
        assert room.receipts == ["fix the login page"]

        room.channel.acknowledges(room.topic, "use the new theme")
        await _until(
            lambda: room.receipts == ["fix the login page", "use the new theme"]
        )
    finally:
        await room.close()


async def test_a_message_read_mid_work_is_not_failed_when_the_session_goes_away():
    """A message sent while the session is working is read at the next tool
    boundary and answered inside that work. When the work ends cleanly and the
    session later goes away (the runner lets an idle session go), nothing has
    failed: the room is not told work ended because the process exited."""
    room = Room(Scripted(_runs_a_tool))
    try:
        await room.send("run the tests")
        await _until(lambda: room.receipts == ["run the tests"])

        second = uuid.uuid4()
        await room.runtime.send(
            room.session,
            "and the linter too",
            system_prompt="",
            acting="cheese",
            work_id=second,
            on_mark=lambda _: None,
            register_input=room.register_input("and the linter too"),
        )
        room.channel.returns(room.topic, "Bash", "42 passed")
        room.channel.acknowledges(room.topic, "and the linter too")
        room.channel.says(room.topic, "tests pass, linter clean")
        room.channel.stops(room.topic, "tests pass, linter clean")
        await _until(lambda: room.results())

        seat = (room.topic, "cheese")
        room.channel.alive = False
        await _until(lambda: room.runtime.tasks[seat].done())
        await _REAL_SLEEP(0.3)

        assert {
            work: event.is_error
            for work, event in room.events
            if isinstance(event, AgentResult)
        } == {room.work: False}
        (read,) = [
            fact
            for fact in room.facts(InputRead)
            if fact.receipt.identity.work_id == second
        ]
        assert read.receipt.execution_work_id == room.work
        assert "session process exited" not in str(room.results())
    finally:
        await room.close()
