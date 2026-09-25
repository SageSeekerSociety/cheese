"""How an open turn is ended when the session will not end it.

Every case runs the real Claude Code runtime, runner stamping, journal mirror
and translation against a scripted session (``StubChannel``); only the clocks
are shortened. What the room receives is what the bound consumer receives.
"""

import asyncio
import time
import uuid

import pytest

from app.domain.agent.harness import CLAUDE_CODE, Opening, SessionRef
from app.domain.agent.harness.driven import runtime as driven_runtime
from app.domain.agent.platform_failures import (
    PROMPT_UNDELIVERED_CODE,
    TURN_TIMEOUT_CODE,
)
from app.domain.agent.service import AgentResult
from tests.conftest import StubChannel

_REAL_SLEEP = asyncio.sleep


class Scripted(StubChannel):
    """A session that does, for the input that opens the turn, only what the
    test scripts — and reads every later input without answering it."""

    def __init__(self, script, **policy: float) -> None:
        super().__init__(**policy)
        self.script = script
        self.opened = False

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        if not self.opened:
            self.opened = True
            self.script(self, topic_id, prompt)

    def begins(self, topic_id: uuid.UUID, prompt: str) -> None:
        """The build takes the input — without echoing it back yet."""
        session = self.sessions[topic_id]
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

        async def consume(_project, _topic, work, event, _eid, _seen, _unsolicited):
            self.events.append((work, event))

        async def receipt(_topic, text):
            self.receipts.append(text)
            self.unread.pop(text, None)

        def oldest_unread(_topic):
            return min(self.unread.values(), default=None)

        self.runtime.bind_events(consume)
        self.runtime.bind_receipts(receipt)
        self.runtime.bind_unread_probe(oldest_unread)

    def results(self) -> list[AgentResult]:
        return [event for _, event in self.events if isinstance(event, AgentResult)]

    async def send(self, text: str) -> None:
        self.unread[text] = time.monotonic()
        await self.runtime.send(
            self.session,
            text,
            Opening(system_prompt=""),
            work_id=self.work,
            on_mark=lambda _: None,
        )

    async def steer(self, text: str) -> None:
        self.unread[text] = time.monotonic()
        assert await self.runtime.deliver(self.topic, text)

    async def close(self) -> None:
        await self.runtime._detach(self.topic)


async def _until(check, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "the condition never came true"
        await _REAL_SLEEP(0.01)


@pytest.fixture
def quick_retries(monkeypatch):
    """The poller's two-second wait between failed reads, cut short."""

    class Quick:
        def __getattr__(self, name):
            return getattr(asyncio, name)

        async def sleep(self, seconds):
            await _REAL_SLEEP(min(seconds, 0.02))

    monkeypatch.setattr(driven_runtime, "asyncio", Quick())


def _talks(channel: Scripted, topic: uuid.UUID, prompt: str) -> None:
    channel.starts(topic)
    channel.acknowledges(topic, prompt)
    channel.says(topic, "still thinking about it")


async def test_talking_without_working_ends_the_turn_once():
    """A session that keeps saying things and never calls a tool or ends is in a
    loop. The turn is ended with the timeout, exactly once — the result the
    interrupted session prints afterwards, and anything later for the same
    work, is not a second ending."""
    room = Room(Scripted(_talks, no_progress_s=0.3))
    try:
        await room.send("fix the login page")
        await _until(lambda: room.results())

        (ended,) = room.results()
        assert ended.is_error and ended.failure_code == TURN_TIMEOUT_CODE
        assert room.work in room.runtime.closed

        room.channel.stops(room.topic, "late news")
        await room.runtime.subscriptions[room.topic].drain()
        assert room.results() == [ended]
    finally:
        await room.close()


def _takes_it_without_echo(channel: Scripted, topic: uuid.UUID, prompt: str) -> None:
    channel.starts(topic)
    channel.begins(topic, prompt)


async def test_an_input_nobody_read_ends_the_turn_undelivered():
    room = Room(Scripted(_takes_it_without_echo, unread_grace_s=0.3))
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


async def test_an_input_waiting_behind_a_running_tool_gets_its_grace_from_the_return():
    """Input is read at tool boundaries. Words said while a long command runs
    cannot be read before it returns, so waiting then is not a failure to
    read — the grace starts when the tool comes back."""
    grace = 0.3
    room = Room(Scripted(_runs_a_tool, unread_grace_s=grace))
    try:
        await room.send("run the tests")
        await _until(lambda: room.receipts == ["run the tests"])
        await room.steer("and the linter too")

        # Several liveness checks go by with the steer unread past its grace.
        await _REAL_SLEEP(2.5)
        assert room.results() == []

        returned_at = time.monotonic()
        room.channel.returns(room.topic, "Bash", "42 passed")
        await _until(lambda: room.results())

        (ended,) = room.results()
        assert ended.failure_code == PROMPT_UNDELIVERED_CODE
        assert time.monotonic() - returned_at >= grace
    finally:
        await room.close()


async def test_a_session_whose_process_exited_ends_the_turn_visibly():
    room = Room(Scripted(_talks))
    try:
        await room.send("fix the login page")
        await _until(lambda: room.channel.sessions[room.topic].working)
        room.channel.alive = False
        await _until(lambda: room.results())

        (ended,) = room.results()
        assert ended.is_error
        assert ended.text == "Claude Code session process exited"
    finally:
        await room.close()


@pytest.mark.usefixtures("quick_retries")
async def test_a_runner_out_of_reach_too_long_ends_the_turn(monkeypatch):
    monkeypatch.setattr(driven_runtime, "RUNNER_GONE_S", 0.5)
    room = Room(Scripted(_talks))
    try:
        await room.send("fix the login page")
        await _until(lambda: room.channel.sessions[room.topic].working)
        # The runner is gone: every call to it now raises DeviceCallError.
        lost_at = time.monotonic()
        room.channel.sessions.pop(room.topic)
        await _until(lambda: room.results())

        (ended,) = room.results()
        assert ended.is_error
        assert ended.text == "Claude Code session process exited"
        assert time.monotonic() - lost_at >= 0.5
    finally:
        await room.close()


@pytest.mark.usefixtures("quick_retries")
async def test_a_runner_back_within_the_limit_keeps_its_turn(monkeypatch):
    monkeypatch.setattr(driven_runtime, "RUNNER_GONE_S", 0.5)
    room = Room(Scripted(_talks))
    try:
        await room.send("fix the login page")
        await _until(lambda: room.channel.sessions[room.topic].working)
        session = room.channel.sessions.pop(room.topic)
        await _REAL_SLEEP(0.2)
        room.channel.sessions[room.topic] = session
        await _REAL_SLEEP(0.6)  # past the limit, counted from the outage
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
    mid-turn the echo comes at the next tool boundary, and only then is the
    message the room showed as waiting actually read."""
    room = Room(Scripted(_takes_it_without_echo))
    try:
        await room.send("fix the login page")
        await _until(lambda: room.channel.sessions[room.topic].working)
        await room.runtime.subscriptions[room.topic].drain()
        assert room.receipts == []

        room.channel.acknowledges(room.topic, "fix the login page")
        await _until(lambda: room.receipts == ["fix the login page"])

        await room.steer("use the new theme")
        await _REAL_SLEEP(0.3)
        await room.runtime.subscriptions[room.topic].drain()
        assert room.receipts == ["fix the login page"]

        room.channel.acknowledges(room.topic, "use the new theme")
        await _until(
            lambda: room.receipts == ["fix the login page", "use the new theme"]
        )
    finally:
        await room.close()
