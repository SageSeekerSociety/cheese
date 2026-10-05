"""A send reuses the seat's session as it is only while its runner answers.

The real room sessions, session core, runner and reads against a scripted
session (``StubChannel``). A runner that holds reads says on every answer
whether its agent process is still there, so a send that follows one can skip
asking the machine again — and must not, once a read has said otherwise or
failed.
"""

import asyncio
import time
import uuid

from tests.unit.test_driven_liveness import Room, Scripted, _until

_REAL_SLEEP = asyncio.sleep


def _answers(channel: Scripted, topic: uuid.UUID, prompt: str) -> None:
    channel.starts(topic)
    channel.acknowledges(topic, prompt)
    channel.says(topic, "done")
    channel.stops(topic, "done")


class Remembering(Scripted):
    """Remembers whether each start was told the seat's runner answers."""

    def __init__(self) -> None:
        super().__init__(_answers)

    @property
    def given(self) -> list[bool]:
        return [screen["runner_alive"] for screen in self.screens]


async def _send(room: Room, text: str) -> None:
    """What a room sends: its own work, from the teammate the seat is for."""
    room.work = uuid.uuid4()
    room.unread[text] = time.monotonic()
    await room.runtime.send(
        room.session,
        text,
        system_prompt="",
        acting="cheese",
        work_id=room.work,
        on_mark=lambda _: None,
        register_input=room.register_input(text),
    )


async def _first_turn(room: Room) -> None:
    await _send(room, "fix the login page")
    await _until(lambda: room.results())
    # The next read is being held at the runner.
    await _REAL_SLEEP(0.3)


async def _stops_vouching(room: Room) -> None:
    """Until the read the runner holds comes back saying otherwise, the seat is
    still one whose runner answers. How soon that read comes back is the
    machine's speed, not the code's, so the test waits for it instead of a
    fixed 0.3 s a loaded runner can outlast."""

    def vouched() -> bool:
        return any(running.answering for running in room.channel.host._running.values())

    await _until(lambda: not vouched())


async def test_a_second_send_to_a_seat_whose_runner_answers_hands_its_session_back():
    channel = Remembering()
    room = Room(channel)
    try:
        await _first_turn(room)
        await _send(room, "and the signup page")
        assert channel.given == [False, True]
    finally:
        await room.close()


async def test_a_seat_whose_agent_process_ended_is_ensured_again():
    channel = Remembering()
    room = Room(channel)
    try:
        await _first_turn(room)
        channel.alive = False
        await _stops_vouching(room)
        channel.alive = True
        await _send(room, "and the signup page")
        assert channel.given == [False, False]
    finally:
        await room.close()


async def test_a_seat_whose_runner_is_gone_is_ensured_again():
    channel = Remembering()
    room = Room(channel)
    try:
        await _first_turn(room)
        channel.drop_session(room.topic)
        await _stops_vouching(room)
        await _send(room, "and the signup page")
        assert channel.given == [False, False]
    finally:
        await room.close()


async def test_a_seat_whose_runner_is_closing_is_ensured_again():
    """The runner answers the read it holds as it starts to close, while its
    agent process is still up and its socket still answers. The next send
    starts the session again rather than being handed a runner on its way out."""
    channel = Remembering()
    room = Room(channel)
    try:
        await _first_turn(room)
        runner = channel._session_for(room.topic)
        runner.closing = True
        runner.announce()
        await _stops_vouching(room)
        # Its replacement, for the send that follows.
        runner.closing = False
        await _send(room, "and the signup page")
        assert channel.given == [False, False]
    finally:
        await room.close()
