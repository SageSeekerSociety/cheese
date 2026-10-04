"""How an open turn is ended when the session will not end it.

Every case runs the real Claude Code runtime, runner stamping, journal mirror
and translation against a scripted session (``StubChannel``); only the clocks
are shortened. What the room receives is what the bound consumer receives.
"""

import asyncio
import time
import uuid
from unittest.mock import AsyncMock

import pytest

from app.domain.agent.harness import CLAUDE_CODE, Opening, SessionRef
from app.domain.agent.harness.driven import runtime as driven_runtime
from app.domain.agent.platform_failures import (
    PROMPT_UNDELIVERED_CODE,
    TURN_TIMEOUT_CODE,
)
from app.domain.agent.service import AgentResult
from app.domain.delivery.input_identity import (
    InputIdentity,
    InputReceipt,
    WorkCompletion,
    WorkTermination,
)
from tests.conftest import StubChannel
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
        self.native_receipts: dict[uuid.UUID, InputReceipt] = {}
        self.completions: list[WorkCompletion] = []
        self.terminations: list[WorkTermination] = []

        async def consume(_project, _topic, work, event, _eid, _seen, _unsolicited):
            self.events.append((work, event))

        async def receipt(evidence: InputReceipt):
            assert evidence.identity == self.identities[evidence.identity.input_id]
            if evidence.evidence == "native_echo":
                input_id = evidence.identity.input_id
                prior = self.native_receipts.get(input_id)
                if prior is not None and evidence.execution_work_id is not None:
                    assert prior.execution_work_id in (None, evidence.execution_work_id)
                if prior is None or evidence.execution_work_id is not None:
                    self.native_receipts[input_id] = evidence
                text = self.inputs[evidence.identity.input_id]
                self.receipts.append(text)
                self.unread.pop(text, None)

        def check_inputs(evidence: WorkCompletion | WorkTermination) -> None:
            assert evidence.input_ids
            assert len(evidence.input_ids) == len(set(evidence.input_ids))
            for input_id in evidence.input_ids:
                identity = self.identities[input_id]
                # A steer can originate in one work and be read in another.
                assert (
                    identity.project_id,
                    identity.topic_id,
                    identity.recipient_handle,
                    identity.harness,
                    identity.native_session_id,
                    identity.input_id,
                ) == (
                    evidence.project_id,
                    evidence.topic_id,
                    evidence.recipient_handle,
                    evidence.harness,
                    evidence.native_session_id,
                    input_id,
                )
                assert (
                    self.native_receipts[input_id].execution_work_id == evidence.work_id
                )

        async def completion(evidence: WorkCompletion):
            check_inputs(evidence)
            self.completions.append(evidence)

        async def termination(evidence: WorkTermination):
            assert evidence.reason in ("interrupted", "is_error")
            check_inputs(evidence)
            self.terminations.append(evidence)

        def oldest_unread(_topic):
            return min(self.unread.values(), default=None)

        self.runtime.bind_reader(
            room_reader(
                events=consume,
                receipts=receipt,
                completions=completion,
                terminations=termination,
            )
        )
        self.runtime.bind_unread_probe(oldest_unread)

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
            Opening(system_prompt=""),
            work_id=self.work,
            on_mark=lambda _: None,
            register_input=self.register_input(text),
        )

    async def steer(self, text: str) -> None:
        self.unread[text] = time.monotonic()
        assert await self.runtime.deliver(
            self.topic, text, register_input=self.register_input(text)
        )

    async def close(self) -> None:
        for seat in list(self.runtime.subscriptions):
            if seat[0] == self.topic:
                await self.runtime._detach(seat)


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
        await room.runtime.subscriptions[(room.topic, "cheese")].drain()
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
        await _until(lambda: room.channel._session_for(room.topic).working)
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
        await _until(lambda: room.channel._session_for(room.topic).working)
        # The runner is gone: every call to it now raises DeviceCallError.
        lost_at = time.monotonic()
        room.channel.drop_session(room.topic)
        await _until(lambda: room.results())

        (ended,) = room.results()
        assert ended.is_error
        # Not the exited-process sentence: nothing said the process exited. The
        # runner simply never answered, which is also what a machine still coming
        # up looks like from here, so the room is told that and not a crash.
        assert ended.text != "Claude Code session process exited"
        assert "没有应答" in ended.text
        assert time.monotonic() - lost_at >= 0.5
    finally:
        await room.close()


@pytest.mark.usefixtures("quick_retries")
async def test_a_runner_back_within_the_limit_keeps_its_turn(monkeypatch):
    monkeypatch.setattr(driven_runtime, "RUNNER_GONE_S", 0.5)
    room = Room(Scripted(_talks))
    try:
        await room.send("fix the login page")
        await _until(lambda: room.channel._session_for(room.topic).working)
        session = room.channel.drop_session(room.topic)
        await _REAL_SLEEP(0.2)
        room.channel.sessions[(room.topic, session.actor)] = session
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
        await _until(lambda: room.channel._session_for(room.topic).working)
        await room.runtime.subscriptions[(room.topic, "cheese")].drain()
        assert room.receipts == []

        room.channel.acknowledges(room.topic, "fix the login page")
        await _until(lambda: room.receipts == ["fix the login page"])

        await room.steer("use the new theme")
        await _REAL_SLEEP(0.3)
        await room.runtime.subscriptions[(room.topic, "cheese")].drain()
        assert room.receipts == ["fix the login page"]

        room.channel.acknowledges(room.topic, "use the new theme")
        await _until(
            lambda: room.receipts == ["fix the login page", "use the new theme"]
        )
    finally:
        await room.close()


async def test_a_message_read_mid_turn_is_not_failed_when_the_session_goes_idle():
    """A message sent while the session is in the middle of a turn is read at the
    next tool boundary and answered inside that turn. When that turn ends and
    the session later goes away (the runner lets an idle session go), nothing
    has failed: the room is not told a turn ended because the process exited."""
    room = Room(Scripted(_runs_a_tool))
    try:
        await room.send("run the tests")
        await _until(lambda: room.receipts == ["run the tests"])

        second = uuid.uuid4()
        await room.runtime.send(
            room.session,
            "and the linter too",
            Opening(system_prompt="", agent_handle="cheese"),
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
        await _until(lambda: seat not in room.runtime.answering)
        await _REAL_SLEEP(0.3)

        # The running turn ended cleanly, and so did the message it took in.
        assert {
            work: event.is_error
            for work, event in room.events
            if isinstance(event, AgentResult)
        } == {room.work: False, second: False}
        assert "session process exited" not in str(room.results())
    finally:
        await room.close()


async def test_a_platform_turn_taken_into_the_running_turn_still_ends():
    """A platform turn waits on its own ending. Taken into a turn already
    running, it is not answered on its own, so when the session goes away it
    is ended then rather than left waiting forever."""
    room = Room(Scripted(_runs_a_tool))
    try:
        await room.send("run the tests")
        await _until(lambda: room.receipts == ["run the tests"])

        ending: list = []

        async def platform_turn() -> None:
            async for event in room.runtime.run_turn(
                project_id=room.session.project_id,
                topic_id=room.topic,
                prompt="tidy the notes",
                system_prompt="",
                resume_session_id=None,
                register_input=room.register_input("tidy the notes"),
                agent_handle="cheese",
                session_agent="cheese",
            ):
                if isinstance(event, AgentResult):
                    ending.append(event)

        waiting = asyncio.create_task(platform_turn())
        await _until(
            lambda: any(
                message.get("type") == "user"
                for message in room.channel._session_for(room.topic).written
                if "tidy the notes" in str(message.get("message"))
            )
        )
        room.channel.returns(room.topic, "Bash", "42 passed")
        room.channel.acknowledges(room.topic, "tidy the notes")
        room.channel.stops(room.topic, "done")
        await _until(lambda: room.results())

        room.channel.alive = False
        await asyncio.wait_for(waiting, 8)
        assert len(ending) == 1
    finally:
        await room.close()
