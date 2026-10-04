"""A session left idle is let go on its machine (``driven.runner``); the room
it belonged to is not told anything went wrong, is not read in the meantime,
and is answered as usual when somebody next speaks.

The real room sessions, session core, mirror and translation, against a
scripted session (``StubChannel``); the runner going away is the stub's session
going.
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


async def _say(room: Room, text: str, teammate: str) -> None:
    await room.runtime.send(
        room.session,
        text,
        system_prompt="",
        acting=teammate,
        work_id=uuid.uuid4(),
        on_mark=lambda _: None,
        register_input=room.register_input(text),
    )


@pytest.mark.usefixtures("quick_retries")
async def test_a_room_whose_session_was_let_go_is_answered_when_spoken_to_again():
    channel = Counting()
    room = Room(channel)
    try:
        await _say(room, "hello", "cheese")
        await _until(lambda: room.results())
        channel.drop_session(room.topic)

        await asyncio.sleep(0.3)
        read = channel.reads
        await asyncio.sleep(1.0)
        # Nobody is waiting on a session that is not there.
        assert channel.reads - read <= 1

        await _say(room, "are you there?", "cheese")
        await _until(lambda: len(room.results()) == 2)
        assert [result.is_error for result in room.results()] == [False, False]
    finally:
        await room.close()


@pytest.mark.usefixtures("quick_retries")
async def test_another_teammate_takes_over_a_seat_whose_session_was_let_go():
    room = Room(StubChannel())
    try:
        await _say(room, "hello", "cheese")
        await _until(lambda: room.results())
        room.channel.drop_session(room.topic)

        await _say(room, "your turn", "cheese-kimi")
        await _until(lambda: len(room.results()) == 2)
        assert [result.is_error for result in room.results()] == [False, False]
    finally:
        await room.close()
