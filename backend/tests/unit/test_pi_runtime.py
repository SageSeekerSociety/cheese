"""A room's turn on pi: sent once, read from a cursor, survivable by the reader.

The entries the fake runner hands back are the recording of a real GLM-5.2 turn,
so what lands in the room here is what a room actually gets.
"""

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.domain.agent.harness import AgentRuntime, Opening, SessionRef
from app.domain.agent.harness.pi.runtime import Handle, PiRuntime
from app.domain.agent.service import AgentResult, AgentSessionInfo, AgentToolUse

ENTRIES = json.loads((Path(__file__).parent / "fixtures/pi-entries.json").read_text())[
    "entries"
]


class Runner:
    """A pi runner as the channel sees it: entries stamped with the work they
    were produced under, and a session that reports whether it is working."""

    def __init__(self):
        self.produced: list[dict] = []
        self.inputs: list[dict] = []
        self.steers: list[dict] = []
        self.working = False
        self.work_id: str | None = None

    async def call(self, handle, method, params):
        if method == "entries":
            since = params.get("since")
            if since is None:
                return {"entries": list(self.produced)}
            ids = [entry["id"] for entry in self.produced]
            return {"entries": self.produced[ids.index(since) + 1 :]}
        if method == "ping":
            return {"alive": True, "working": self.working, "work_id": self.work_id}
        if method == "abort":
            self.working = False
            return {"aborted": True}
        if method == "steer":
            self.steers.append(params)
            return {"ok": True}
        assert method == "send"
        self.inputs.append(params)
        self.working = True
        self.work_id = params["work_id"]
        self.produced.extend(
            {**entry, "cheese": {"work_id": params["work_id"]}} for entry in ENTRIES
        )
        return {"ok": True}


def wire(tmp_path):
    session = SessionRef(uuid.uuid4(), uuid.uuid4())
    handle = Handle(
        session,
        "device",
        "/state",
        "pi-session",
        "teammate",
        tmp_path / "mirror" / "entries.sqlite",
    )
    runner = Runner()
    channel = AsyncMock()
    channel.ensure.return_value = handle
    channel.images.return_value = []
    channel.call.side_effect = runner.call
    channel.discover.return_value = [handle]
    runtime = PiRuntime(channel)
    assert isinstance(runtime, AgentRuntime)
    return session, runtime, runner


@pytest.mark.anyio
async def test_a_turn_lands_under_one_owner_and_ends_once(tmp_path):
    session, runtime, runner = wire(tmp_path)
    consumer, receipts, activity = AsyncMock(), AsyncMock(), AsyncMock()
    runtime.bind_events(consumer)
    runtime.bind_receipts(receipts)
    runtime.bind_activity(activity)
    work = uuid.uuid4()

    assert await runtime.send(
        session,
        "改一下 greet",
        Opening("system"),
        work_id=work,
        on_mark=lambda _: None,
    )
    await runtime.close(session)

    assert [call["text"] for call in runner.inputs] == ["改一下 greet"]
    receipts.assert_awaited_once_with(session.topic_id, "改一下 greet")

    landed = [call.args for call in consumer.await_args_list]
    assert {args[2] for args in landed} == {work}, "every event belongs to this turn"
    events = [args[3] for args in landed]
    assert isinstance(events[0], AgentSessionInfo)
    assert [e.name for e in events if isinstance(e, AgentToolUse)] == [
        "write",
        "read",
        "edit",
        "bash",
    ]
    results = [e for e in events if isinstance(e, AgentResult)]
    assert len(results) == 1 and not results[0].is_error
    assert results[0].usage is not None and results[0].usage.cost_usd > 0

    # 「在干活 / 停了」 is read out of the log, not reported separately.
    assert [call.args[3] for call in activity.await_args_list] == [True, False]


@pytest.mark.anyio
async def test_a_session_that_outlived_the_backend_is_read_not_restarted(tmp_path):
    """The coroutine waiting on the turn died with the process; the session did
    not. Recovery establishes that we are listening — it must never re-send."""
    session, runtime, runner = wire(tmp_path)
    work = str(uuid.uuid4())
    runner.produced.extend({**entry, "cheese": {"work_id": work}} for entry in ENTRIES)
    runner.working, runner.work_id = True, work

    consumer = AsyncMock()
    runtime.bind_events(consumer)
    runtime.bind_activity(AsyncMock())
    recovered = await runtime.recover("device")
    assert recovered == [session]
    assert runtime.holds(session.topic_id)

    await runtime.replay(session, known_texts=set())
    await runtime.close(session)

    events = [call.args[3] for call in consumer.await_args_list]
    assert [e.name for e in events if isinstance(e, AgentToolUse)] == [
        "write",
        "read",
        "edit",
        "bash",
    ]
    assert any(isinstance(event, AgentResult) for event in events)
    assert {call.args[2] for call in consumer.await_args_list} == {uuid.UUID(work)}
    assert runner.inputs == [], "recovery must not send the prompt again"


@pytest.mark.anyio
async def test_a_person_talking_mid_turn_steers_rather_than_starting_a_turn(tmp_path):
    session, runtime, runner = wire(tmp_path)
    runtime.bind_events(AsyncMock())
    runtime.bind_activity(AsyncMock())
    work = uuid.uuid4()

    assert await runtime.deliver(session.topic_id, "等一下") is False, (
        "there is no session here yet"
    )
    await runtime.send(
        session, "开始", Opening("system"), work_id=work, on_mark=lambda _: None
    )
    assert await runtime.deliver(session.topic_id, "换个名字") is True
    assert [call["text"] for call in runner.steers] == ["换个名字"]
    assert len(runner.inputs) == 1, "steering is not a second turn"
    await runtime.close(session)


@pytest.mark.anyio
async def test_interrupt_takes_the_work_without_taking_the_session(tmp_path):
    session, runtime, runner = wire(tmp_path)
    runtime.bind_events(AsyncMock())
    runtime.bind_activity(AsyncMock())
    await runtime.send(
        session,
        "开始",
        Opening("system"),
        work_id=uuid.uuid4(),
        on_mark=lambda _: None,
    )
    assert await runtime.interrupt(session) is True
    assert runner.working is False
    # Weaker than close: the session is still ours to send into.
    assert runtime.holds(session.topic_id)
    await runtime.close(session)
    assert not runtime.holds(session.topic_id)
