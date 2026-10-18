"""A turn that is thinking is not a turn that stopped.

The rail's red light asks whether a room still running shows no sign of life
for five minutes. A long think, a twenty-minute command, a machine still coming
up: none of them writes a frame for the whole of it, so counting frames alone
turned live turns red. Reading a session answers every ``READ_WAIT_S`` even
when it has nothing to say, and that answer is the second sign of life.
"""

import time
import uuid
from collections import deque

import httpx
import pytest

from app.domain.agent.harness import SessionRef as Seat
from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.session_host import host as session_host
from app.domain.agent.session_host.contract import SessionRef
from app.domain.agent.session_host.driver import Launched
from app.domain.agent.turn.intake.status import WorkStatus
from app.domain.agent.turn.state.execution import ExecutionState


def _state() -> ExecutionState:
    return ExecutionState(
        recent=deque(),
        live={},
        last_frame_at={},
        live_topics={},
        delivered=set(),
        host_failed_topics=set(),
    )


@pytest.mark.anyio
async def test_an_answer_keeps_a_quiet_turn_alive() -> None:
    broker = InProcessBroker()
    room = str(uuid.uuid4())
    await broker.publish(room, {"type": "turn_started", "turn_id": "turn-1"})
    # Ten minutes of thinking: no frame, while the reading kept answering.
    broker._last_activity_at[room] = time.monotonic() - 600
    broker.note_alive(room)

    snapshot = broker.activity_snapshot(room)
    assert snapshot is not None
    # Two clocks on purpose: the room is quiet, the turn is alive.
    assert snapshot["idle_for_s"] >= 600
    assert snapshot["alive_for_s"] < 5

    work = WorkStatus(broker, _state()).live_work_for_topic(uuid.UUID(room))
    assert work is not None
    assert work["silent_for_s"] < 5


@pytest.mark.anyio
async def test_a_turn_nobody_hears_stays_quiet() -> None:
    broker = InProcessBroker()
    room = str(uuid.uuid4())
    await broker.publish(room, {"type": "turn_started", "turn_id": "turn-1"})
    broker._last_activity_at[room] = time.monotonic() - 600

    work = WorkStatus(broker, _state()).live_work_for_topic(uuid.UUID(room))
    assert work is not None
    assert work["silent_for_s"] >= 600


@pytest.mark.anyio
async def test_an_answer_with_no_turn_running_is_not_counted() -> None:
    """A seated session answers even when its room has no turn in flight; only
    an answer that has a turn to keep alive counts."""
    broker = InProcessBroker()
    room = str(uuid.uuid4())
    broker.note_alive(room)
    assert broker.activity_snapshot(room) is None


@pytest.mark.anyio
async def test_a_new_turn_does_not_inherit_the_last_one_s_answer() -> None:
    broker = InProcessBroker()
    room = str(uuid.uuid4())
    await broker.publish(room, {"type": "turn_started", "turn_id": "turn-1"})
    broker.note_alive(room)
    await broker.publish(room, {"type": "turn_finished", "turn_id": "turn-1"})

    await broker.publish(room, {"type": "turn_started", "turn_id": "turn-2"})
    broker._last_activity_at[room] = time.monotonic() - 600
    snapshot = broker.activity_snapshot(room)
    assert snapshot is not None
    assert snapshot["alive_for_s"] is None


@pytest.mark.anyio
async def test_a_turn_taken_from_the_books_is_alive_when_it_answers() -> None:
    """A turn adopted from the database has published no frame here yet: its
    frame clock says nothing at all, and counting only frames read that as a
    stopped turn from the start."""
    broker = InProcessBroker()
    room = str(uuid.uuid4())
    broker.adopt(room, [("turn-1", time.time() - 600, None)], read_at=0)
    assert broker.activity_snapshot(room)["idle_for_s"] is None
    broker.note_alive(room)

    work = WorkStatus(broker, _state()).live_work_for_topic(uuid.UUID(room))
    assert work is not None
    assert work["silent_for_s"] < 5


@pytest.mark.anyio
async def test_two_clocks_that_say_nothing_make_no_verdict() -> None:
    broker = InProcessBroker()
    room = str(uuid.uuid4())
    broker.adopt(room, [("turn-1", time.time() - 600, None)], read_at=0)

    work = WorkStatus(broker, _state()).live_work_for_topic(uuid.UUID(room))
    assert work is not None
    assert work["silent_for_s"] is None


_answers: list[uuid.UUID] = []


async def _pump_once(room: uuid.UUID, subscription) -> None:
    """Run one pass of the reading for ``room``, stopping after it."""
    host = session_host.SessionHost(hub=object(), on_alive=_answers.append)
    ref = SessionRef("pi", "rooms/a-room/pi")
    running = session_host._Running(
        "center",
        Launched("", frozenset(), ""),
        Seat(uuid.uuid4(), room, harness="pi"),
        "",
        None,
        stopping=True,
    )

    async def hand(read) -> None:
        return None

    await host._pump(ref, running, subscription, hand, False)


@pytest.mark.anyio
async def test_reading_a_session_says_when_its_runner_answers() -> None:
    room = uuid.uuid4()
    _answers.clear()

    class Subscription:
        heard = {"alive": True}

        async def drain(self, wait: float = 0.0) -> int:
            return 0

    await _pump_once(room, Subscription())
    assert _answers == [room]


@pytest.mark.anyio
async def test_a_read_out_of_reach_is_not_an_answer() -> None:
    _answers.clear()

    class Subscription:
        heard: dict = {}

        async def drain(self, wait: float = 0.0) -> int:
            raise httpx.TransportError("Server disconnected")

    await _pump_once(uuid.uuid4(), Subscription())
    assert _answers == []
