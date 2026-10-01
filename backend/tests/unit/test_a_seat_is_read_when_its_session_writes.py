"""A seat's session is read when it writes, not on a timer.

The real Claude Code runtime, runner, mirror and translation, against a scripted
session (``StubChannel``): the runner holds each read until its session writes
something, so a quiet seat costs one read per wait, and what the session writes
— a record, or the block it is in the middle of writing — reaches the room at
once.
"""

import asyncio
import dataclasses
import time
import uuid

from app.domain.agent.harness.driven import runtime as driven_runtime
from app.domain.agent.service import AgentMessage, AgentToolUse
from tests.unit.test_driven_liveness import Room, Scripted, _until

_REAL_SLEEP = asyncio.sleep


def _working(channel: Scripted, topic: uuid.UUID, prompt: str) -> None:
    """The session takes the input, says it is on it, and goes on working."""
    channel.starts(topic)
    channel.acknowledges(topic, prompt)
    channel.says(topic, "on it")


class Counting(Scripted):
    def __init__(self, script=_working, **policy: float) -> None:
        super().__init__(script, **policy)
        self.asked: list[tuple[str, dict]] = []

    async def call(self, handle, method, params):
        self.asked.append((method, dict(params)))
        return await super().call(handle, method, params)


class OldRunners(Counting):
    """Runners started before runners could hold a read: they say nothing of
    it when greeted."""

    async def ensure(self, session, opening, live=None):
        handle = await super().ensure(session, opening, live)
        return dataclasses.replace(handle, capabilities=frozenset())


def _said(room: Room) -> list[str]:
    return [event.text for _, event in room.events if isinstance(event, AgentMessage)]


async def test_a_seat_with_a_turn_open_and_nothing_new_costs_one_read_per_wait(
    monkeypatch,
):
    monkeypatch.setattr(driven_runtime, "READ_WAIT_S", 1.0)
    channel = Counting()
    room = Room(channel)
    try:
        await room.send("fix the login page")
        await _until(lambda: _said(room) == ["on it"])
        await _REAL_SLEEP(0.3)

        before = len(channel.asked)
        await _REAL_SLEEP(3.0)
        assert room.results() == []
        # Three waits, and a read at each end of them at most.
        assert len(channel.asked) - before <= 4, channel.asked[before:]
    finally:
        await room.close()


async def test_a_record_the_session_writes_reaches_the_room_at_once():
    channel = Counting()
    room = Room(channel)
    try:
        await room.send("fix the login page")
        await _until(lambda: _said(room) == ["on it"])
        # Long past any read on a timer: the read is held at the runner now.
        await _REAL_SLEEP(1.0)

        written_at = time.monotonic()
        channel.uses(room.topic, "Bash", command="make test")
        await _until(
            lambda: any(isinstance(event, AgentToolUse) for _, event in room.events),
            timeout=2.0,
        )
        assert time.monotonic() - written_at < 0.5
    finally:
        await room.close()


async def test_an_agent_process_that_dies_writing_nothing_ends_the_turn_at_once():
    channel = Counting()
    room = Room(channel)
    try:
        await room.send("fix the login page")
        await _until(lambda: _said(room) == ["on it"])
        # The read is held at the runner now, far longer than the wait below.
        await _REAL_SLEEP(1.0)

        died_at = time.monotonic()
        channel.alive = False
        await _until(lambda: room.results(), timeout=2.0)
        assert time.monotonic() - died_at < 0.5
        (ended,) = room.results()
        assert ended.is_error
        assert ended.text == "Claude Code session process exited"
    finally:
        await room.close()


async def test_an_old_runner_is_read_at_a_fixed_rate_and_never_asked_to_wait():
    channel = OldRunners()
    room = Room(channel)
    try:
        await room.send("fix the login page")
        await _until(lambda: _said(room) == ["on it"])

        before = len(channel.asked)
        await _REAL_SLEEP(1.0)
        reads = [call for call in channel.asked[before:] if call[0] == "events"]
        # Read at the old floor while its turn is open: not a spin.
        assert 3 <= len(reads) <= 15, len(reads)

        channel.stops(room.topic, "done")
        await _until(lambda: room.results())
        await _REAL_SLEEP(0.3)
        before = len(channel.asked)
        await _REAL_SLEEP(1.5)
        # And far more slowly once nothing is open.
        assert len(channel.asked) - before <= 1

        assert not any("wait" in params for _, params in channel.asked)
    finally:
        await room.close()


async def test_what_the_agent_is_writing_reaches_the_room_as_it_grows_and_goes_when_it_lands():  # noqa: E501
    channel = Counting()
    room = Room(channel)
    shown: list[tuple[uuid.UUID | None, str, list[dict], list[str]]] = []

    async def live(topic, work, agent, blocks):
        assert topic == room.topic
        shown.append((work, agent, blocks, _said(room)))

    room.runtime.bind_live(live)
    try:
        await room.send("fix the login page")
        await _until(lambda: _said(room) == ["on it"])
        runner = channel._session_for(room.topic)

        def stream(event: dict) -> None:
            runner.stream(
                {"type": "stream_event", "parent_tool_use_id": None, "event": event}
            )

        stream(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            }
        )
        for piece in ("Hel", "lo, ", "world"):
            stream(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": piece},
                }
            )
            await _REAL_SLEEP(0.3)
        texts = [blocks[0]["text"] for _, _, blocks, _ in shown if blocks]
        assert texts and texts[-1] == "Hello, world"
        assert len(texts) >= 2 and all(
            later.startswith(earlier)
            for earlier, later in zip(texts, texts[1:], strict=False)
        )
        assert {work for work, _, _, _ in shown} == {room.work}

        channel.says(room.topic, "Hello, world")
        await _until(lambda: shown[-1][2] == [])
        # What was shown goes once the finished message is in the room, not
        # before it.
        assert shown[-1][3] == ["on it", "Hello, world"]
        # Nothing of it was ever a record.
        assert _said(room) == ["on it", "Hello, world"]
        records = [row["record"] for row in runner.journal.read(0)]
        assert not any(record.get("type") == "stream_event" for record in records)
    finally:
        await room.close()


async def test_a_subagents_writing_and_reasoning_are_not_shown():
    channel = Counting()
    room = Room(channel)
    shown: list[list[dict]] = []

    async def live(topic, work, agent, blocks):
        shown.append(blocks)

    room.runtime.bind_live(live)
    try:
        await room.send("fix the login page")
        await _until(lambda: _said(room) == ["on it"])
        runner = channel._session_for(room.topic)
        runner.stream(
            {
                "type": "stream_event",
                "parent_tool_use_id": "toolu_parent",
                "event": {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": "a subagent"},
                },
            }
        )
        runner.stream(
            {
                "type": "stream_event",
                "parent_tool_use_id": None,
                "event": {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "thinking", "thinking": ""},
                },
            }
        )
        runner.stream(
            {
                "type": "stream_event",
                "parent_tool_use_id": None,
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": "hmm"},
                },
            }
        )
        await _REAL_SLEEP(0.5)
        assert all(blocks == [] for blocks in shown)
    finally:
        await room.close()
