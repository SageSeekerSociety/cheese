"""A room's turn on pi: sent once, read from a cursor, survivable by the reader.

The entries the fake runner hands back are the recording of a real GLM-5.2 turn,
so what lands in the room here is what a room actually gets.
"""

import asyncio
import json
import logging
import threading
import uuid
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import anyio
import pytest

from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.agent.harness import AgentRuntime, Opening, SessionRef
from app.domain.agent.harness.driven import runtime as driven_runtime
from app.domain.agent.harness.driven.runner import LONG_POLL
from app.domain.agent.harness.pi.journal import Journal
from app.domain.agent.harness.pi.runtime import Handle, PiRuntime
from app.domain.agent.service import AgentResult, AgentSessionInfo, AgentToolUse
from app.domain.delivery.input_identity import InputIdentity, InputReceipt
from tests.support.hang import HANG_S

ENTRIES = json.loads((Path(__file__).parent / "fixtures/pi-entries.json").read_text())[
    "entries"
]


class Runner:
    """A pi runner as the channel sees it: entries stamped with the work they
    were produced under, and a session that reports whether it is working."""

    def __init__(self):
        self.produced: list[dict] = []
        self.reads = 0
        self.inputs: list[dict] = []
        self.steers: list[dict] = []
        self.working = False
        self.work_id: str | None = None
        # The device's link is down: every call to it raises, as the hub does.
        self.offline = False
        self.refused = 0

    async def call(self, handle, method, params):
        if self.offline:
            self.refused += 1
            raise DeviceOffline("device")
        if method == "entries":
            self.reads += 1
            since = params.get("since")
            ids = [entry["id"] for entry in self.produced]
            after = 0 if since is None else ids.index(since) + 1
            # Held, as a runner holds a read, until there is something past it.
            deadline = asyncio.get_running_loop().time() + params.get("wait", 0)
            while (
                len(self.produced) <= after
                and asyncio.get_running_loop().time() < deadline
            ):
                await asyncio.sleep(0.01)
            return {"entries": self.produced[after:]}
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
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), "teammate", harness="pi")
    handle = Handle(
        session,
        "device",
        "/state",
        "pi-session",
        "teammate",
        tmp_path / "mirror" / "entries.sqlite",
        frozenset({LONG_POLL}),
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
    register_input = AsyncMock()
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
        register_input=register_input,
    )
    await runtime.close(session)

    assert [call["text"] for call in runner.inputs] == ["改一下 greet"]
    identity = InputIdentity(
        session.project_id,
        session.topic_id,
        "teammate",
        "pi",
        "pi-session",
        work,
        work,
    )
    register_input.assert_awaited_once_with(identity)
    receipts.assert_awaited_once_with(InputReceipt(identity, "accepted"))

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
@pytest.mark.parametrize("failure", [DeviceCallError, DeviceOffline, TimeoutError])
async def test_recovery_continues_when_a_discovered_runner_disappears(
    tmp_path, failure
):
    session, runtime, runner = wire(tmp_path)
    channel = cast(AsyncMock, runtime.channel)
    retained = channel.discover.return_value[0]
    dead = Handle(
        SessionRef(session.project_id, uuid.uuid4(), harness="pi"),
        "device",
        "/dead",
        "dead",
        "other",
        tmp_path / "dead" / "entries.sqlite",
        frozenset({LONG_POLL}),
    )
    channel.discover.return_value = [dead, retained]

    async def call(handle, method, params):
        if handle == dead:
            raise failure("device")
        return await runner.call(handle, method, params)

    channel.call.side_effect = call
    assert await runtime.recover("device") == [session]
    assert runtime.holds(session.topic_id)
    assert not runtime.holds(dead.session.topic_id)
    assert runner.inputs == []
    await runtime.close(session)


@pytest.mark.anyio
async def test_a_person_talking_mid_turn_steers_rather_than_starting_a_turn(tmp_path):
    session, runtime, runner = wire(tmp_path)
    runtime.bind_events(AsyncMock())
    runtime.bind_activity(AsyncMock())
    runtime.bind_receipts(AsyncMock())
    register_input = AsyncMock()
    work = uuid.uuid4()

    assert (
        await runtime.deliver(session.topic_id, "等一下", register_input=register_input)
        is False
    ), "there is no session here yet"
    await runtime.send(
        session,
        "开始",
        Opening("system"),
        work_id=work,
        on_mark=lambda _: None,
        register_input=register_input,
    )
    assert (
        await runtime.deliver(
            session.topic_id, "换个名字", register_input=register_input
        )
        is True
    )
    assert [call["text"] for call in runner.steers] == ["换个名字"]
    assert len(runner.inputs) == 1, "steering is not a second turn"
    await runtime.close(session)


@pytest.mark.anyio
async def test_interrupt_takes_the_work_without_taking_the_session(tmp_path):
    session, runtime, runner = wire(tmp_path)
    runtime.bind_events(AsyncMock())
    runtime.bind_activity(AsyncMock())
    runtime.bind_receipts(AsyncMock())
    await runtime.send(
        session,
        "开始",
        Opening("system"),
        work_id=uuid.uuid4(),
        on_mark=lambda _: None,
        register_input=AsyncMock(),
    )
    assert await runtime.interrupt(session) is True
    assert runner.working is False
    # Weaker than close: the session is still ours to send into.
    assert runtime.holds(session.topic_id)
    await runtime.close(session)
    assert not runtime.holds(session.topic_id)


@pytest.mark.anyio
async def test_a_quiet_room_reads_slower(tmp_path):
    """Reading an entry log is a call to the room's device, and a room nobody
    is talking to answers it with an empty page. At a fixed 100ms that is ten
    calls a second per room for nothing, so a quiet log has to cost less.
    """
    session, runtime, runner = wire(tmp_path)
    runtime.bind_events(AsyncMock())
    runtime.bind_activity(AsyncMock())
    runtime.bind_receipts(AsyncMock())

    await runtime.recover("device")
    await runtime.replay(session, known_texts=set())

    await asyncio.sleep(2.5)
    quiet = runner.reads
    assert quiet <= 10, f"a quiet room was read {quiet} times in 2.5s"

    await runtime.close(session)


@pytest.mark.anyio
async def test_a_new_turn_is_read_at_once_in_a_quiet_room(tmp_path, monkeypatch):
    """The room still has to answer the moment someone sends into it, however
    long its runner holds a read.

    A read is held for an hour here, so a turn's entries can only land within
    the wait below if what the session wrote answered the held read. A
    deadline shorter than the hold would measure how fast the machine running
    the suite is instead.
    """
    monkeypatch.setattr(driven_runtime, "READ_WAIT_S", 3600.0)
    session, runtime, runner = wire(tmp_path)
    consumer = AsyncMock()
    runtime.bind_events(consumer)
    runtime.bind_activity(AsyncMock())
    runtime.bind_receipts(AsyncMock())

    await runtime.recover("device")
    await runtime.replay(session, known_texts=set())
    # The reader has read the empty log once and is now waiting out its interval.
    with anyio.fail_after(HANG_S):
        while runner.reads == 0:
            await asyncio.sleep(0.01)

    assert await runtime.send(
        session,
        "开始",
        Opening("system"),
        work_id=uuid.uuid4(),
        on_mark=lambda _: None,
        register_input=AsyncMock(),
    )
    landed: list = []
    with anyio.fail_after(HANG_S):
        while not any(isinstance(event, AgentToolUse) for event in landed):
            await asyncio.sleep(0.01)
            landed = [call.args[3] for call in consumer.await_args_list]

    await runtime.close(session)


@pytest.mark.anyio
async def test_a_send_that_lands_as_the_reader_starts_to_wait_is_read_at_once(
    tmp_path, monkeypatch
):
    """The moment between the reader taking its turn and asking the runner to
    hold the read is still a quiet room: a send landing there is read at once,
    not after the hold."""
    monkeypatch.setattr(driven_runtime, "READ_WAIT_S", 3600.0)
    # Once armed, the reader stops on its way to the held read until the send
    # is in. Before that the runner answers at once, so the reader keeps
    # coming back past that point.
    armed, on_its_way, go_on = threading.Event(), threading.Event(), threading.Event()
    recall = Journal.recall

    def slow_recall(journal, name):
        if armed.is_set() and not go_on.is_set():
            on_its_way.set()
            go_on.wait(HANG_S)
        return recall(journal, name)

    monkeypatch.setattr(Journal, "recall", slow_recall)
    session, runtime, runner = wire(tmp_path)

    async def call(handle, method, params):
        if method == "entries" and not armed.is_set():
            params = {**params, "wait": 0}
        return await runner.call(handle, method, params)

    cast(AsyncMock, runtime.channel.call).side_effect = call
    consumer = AsyncMock()
    runtime.bind_events(consumer)
    runtime.bind_activity(AsyncMock())
    runtime.bind_receipts(AsyncMock())

    await runtime.recover("device")
    await runtime.replay(session, known_texts=set())
    armed.set()
    with anyio.fail_after(HANG_S):
        while not on_its_way.is_set():
            await asyncio.sleep(0.01)

    sending = asyncio.ensure_future(
        runtime.send(
            session,
            "开始",
            Opening("system"),
            work_id=uuid.uuid4(),
            on_mark=lambda _: None,
            register_input=AsyncMock(),
        )
    )
    await asyncio.sleep(0.1)
    go_on.set()
    landed: list = []
    with anyio.fail_after(HANG_S):
        assert await sending
        while not any(isinstance(event, AgentToolUse) for event in landed):
            await asyncio.sleep(0.01)
            landed = [call.args[3] for call in consumer.await_args_list]

    with anyio.fail_after(HANG_S):
        await runtime.close(session)


@pytest.mark.anyio
async def test_a_device_that_went_offline_is_not_reported_as_a_read_failure(
    tmp_path, caplog
):
    """A room whose machine is off is what the poller waits for.

    Reported as an exception it was two ERROR lines a second per topic, and
    every ERROR line is an alert: 37 of the 53 messages in the alert channel on
    2026-09-16 were this one, named after a journal read rather than an absent
    device.
    """
    session, runtime, runner = wire(tmp_path)
    runtime.bind_events(AsyncMock())
    runtime.bind_receipts(AsyncMock())
    runtime.bind_activity(AsyncMock())

    assert await runtime.send(
        session,
        "改一下 greet",
        Opening("system"),
        work_id=uuid.uuid4(),
        on_mark=lambda _: None,
        register_input=AsyncMock(),
    )

    with caplog.at_level(logging.DEBUG, logger="app.domain.agent.harness.pi.runtime"):
        caplog.clear()
        runner.offline = True
        # The poller has come round to the absent device a second time, which
        # is what would repeat the line.
        with anyio.fail_after(HANG_S):
            while runner.refused < 2:
                await asyncio.sleep(0.01)

        errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors == []
        waits = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(waits) == 1, [r.getMessage() for r in waits]
        assert "waiting for the device" in waits[0].getMessage()

        runner.offline = False

        def resumed() -> list[logging.LogRecord]:
            return [
                r
                for r in caplog.records
                if r.levelno == logging.INFO and "resumed" in r.getMessage()
            ]

        # The loop can be mid-way through its retry wait when the device comes
        # back; the resume lands on its next pass, not at once.
        with anyio.fail_after(HANG_S):
            while not resumed():
                await asyncio.sleep(0.01)
        assert len(resumed()) == 1, [r.getMessage() for r in caplog.records]

    await runtime.close(session)
