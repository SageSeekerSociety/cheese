"""A room with no turn open is not read every second; it is read when its
session writes something.

What a session writes between turns (a background task finishing, a turn it
opens itself) is rung for by its runner (``driven.runner``), and the ring wakes
the read. The real Claude Code runtime, mirror and translation, against a
scripted session (``StubChannel``).
"""

import asyncio
import uuid

import pytest

from tests.conftest import StubChannel
from tests.unit.test_driven_liveness import Room, _until, quick_retries

__all__ = ["quick_retries"]


class Counting(StubChannel):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    async def call(self, handle, method, params):
        if method == "events":
            self.reads += 1
        return await super().call(handle, method, params)


@pytest.mark.usefixtures("quick_retries")
async def test_a_quiet_room_is_read_rarely_and_at_once_when_its_session_writes():
    channel = Counting()
    room = Room(channel)
    try:
        await room.send("hello")
        await _until(lambda: room.results())

        # Long enough for the read to have backed off past a second.
        await asyncio.sleep(6.0)
        read = channel.reads
        await asyncio.sleep(3.0)
        assert channel.reads - read <= 1

        # The session does something on its own, and its runner rings
        # (the scripted session rings as it writes).
        before = len(room.events)
        channel.uses(room.topic, "Bash", command="make test")
        await _until(lambda: len(room.events) > before, timeout=0.5)
    finally:
        await room.close()


@pytest.mark.usefixtures("quick_retries")
async def test_a_ring_for_a_seat_nobody_reads_here_wakes_nothing():
    room = Room(StubChannel())
    try:
        assert not room.runtime.wake(uuid.uuid4(), "cheese")
        assert not room.runtime.wake(room.topic, "somebody-else")
    finally:
        await room.close()
