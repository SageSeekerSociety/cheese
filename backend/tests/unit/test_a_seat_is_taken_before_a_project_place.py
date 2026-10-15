"""A turn waits for its seat before it takes one of the project's places.

Two messages in one conversation used to take two of them: the second took a
place, found its seat held by the first, and kept the place until the first
turn ended — a turn that could not run, holding one of the places every
conversation of the project shares (#1561). The place is now asked for only
once the seat is in hand, and a turn that waits for the seat says so.
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.core.sentences import say
from app.domain.agent import admission
from app.domain.agent.admission import HOST_BUSY_META, Lease
from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.agent.seat_admission import seat_admission
from app.domain.agent.turn.intake import messages
from tests.support.hang import HANG_S


@pytest.fixture(autouse=True)
def without_valkey(monkeypatch):
    """Seat and places are held in this process: what is watched here is the
    order a turn asks for them in, not where they are held."""
    monkeypatch.setattr(admission, "get_redis_client", lambda: None)
    monkeypatch.setattr(messages, "get_redis_client", lambda: None)


class _Room:
    """The admission half of a chat service: a work policy, one seat, and the
    events the room is told with. No turn is run here."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.metas: list[dict | None] = []
        self.seat = Lease(f"admission:seat:{uuid.uuid4()}:cheese-seat")
        #: Never opened: the turns here come in without a delivery, which is
        #: the only thing that reads the database on the way in.
        self.session_factory = None

    async def work_policy(self, topic_id, agent=None):
        return {
            "project_id": "proj-1",
            "max_concurrent_turns": 1,
            "credits_exhausted": False,
        }

    @property
    def live(self):
        return SimpleNamespace(seat_lock_for=lambda topic_id, handle: self.seat)

    async def post_system_event(self, topic_id, text, turn_id=None, *, meta=None):
        self.events.append(str(text))
        self.metas.append(meta)
        return None


async def _until(cond, timeout: float = HANG_S) -> None:
    async with asyncio.timeout(timeout):
        while not cond():
            await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_a_turn_waiting_for_its_seat_takes_no_project_place(monkeypatch):
    room = _Room()
    intake = AgentWorkRunner(InProcessBroker(), turn_timeout_s=5.0)._intake

    async def resolve_seat(service, topic_id, **kwargs):
        return "cheese-seat"

    monkeypatch.setattr(intake, "_resolve_initial_seat", resolve_seat)

    places: list[str] = []

    async def a_place(redis, ticket, **kwargs):
        """The place among the project's turns, taken the moment it is asked
        for: when a turn asks for one is what this test watches."""
        places.append(ticket)
        return None

    monkeypatch.setattr(messages, "enter", a_place)

    topic_id, turn_id = uuid.uuid4(), uuid.uuid4()
    running, leave = asyncio.Event(), asyncio.Event()

    async def first_turn() -> None:
        async with seat_admission(room.live.seat_lock_for(topic_id, "cheese-seat")):
            running.set()
            await leave.wait()

    verdicts: list[str] = []

    async def a_turn_goes_in() -> None:
        """The second message of the conversation, admitted and let go of in
        this one task: a turn gives up what it was let in by where it ran."""
        verdict, admitted = await intake._admit(room, topic_id, turn_id)
        verdicts.append(verdict)
        await admitted.release()

    # A turn of this conversation is inside the seat, as a running one is. The
    # second message of the conversation arrives while it is.
    held = asyncio.create_task(first_turn())
    await running.wait()
    waiting = asyncio.create_task(a_turn_goes_in())

    # It queued, in the words the room already reads when a resource rather
    # than a queue position is what holds a turn back, and it has asked for no
    # place of the project's: that place would be held for as long as it waits
    # for the seat.
    await _until(lambda: room.events)
    assert room.events[0] == str(say("turnQueuedHostBusy"))
    assert room.metas[0] == HOST_BUSY_META
    assert places == []

    leave.set()
    await held
    await waiting

    # The place is asked for once the seat is free, and only then.
    assert verdicts == ["ok"]
    assert places == [str(turn_id)]
    assert not room.seat.locked()
