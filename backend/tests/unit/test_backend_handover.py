"""Handing the running work from one backend process to the next (#1723).

A rollout overlaps two backends on one database: the incoming one starts and
serves before the outgoing one has stopped. What must hold across that, stated
the way a person running the platform would state it:

- only one of them owns the running work at a time, and the other takes it over
  when the owner leaves, whether it leaves in an orderly way or dies;
- a message that reaches the incoming backend before it has taken over is
  answered once it has, not by a turn started blind next to a running one;
- the outgoing backend lets go of a running turn without ending it: the turn is
  still there for the incoming one to pick up, and nothing the outgoing one was
  still sending arrives after it let go;
- a session's output lands in the room once, through whichever backend listens
  to it after the handover — never through both.
"""

import asyncio
import time
import uuid

import pytest
from sqlalchemy import text

from app.core.ownership import Ownership
from app.domain.agent.harness.claude_code.runtime import ClaudeCodeRuntime
from app.domain.agent.runtime import (
    AgentWorkRunner,
    InProcessBroker,
    addressed_to_agent,
)
from app.domain.agent.service import AgentResult
from tests.turn_log import a_topic, open_turn_ids
from tests.unit.test_driven_liveness import Room, Scripted


def _url(db_factory) -> str:
    return db_factory.kw["bind"].url.render_as_string(hide_password=False)


async def _until(check, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "the condition never came true"
        await asyncio.sleep(0.01)


async def _until_open(db_factory, count: int) -> None:
    deadline = time.monotonic() + 5
    while len(await open_turn_ids(db_factory)) != count:
        assert time.monotonic() < deadline, "the turn never opened"
        await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_one_backend_owns_the_running_work_and_the_next_takes_it_over(
    db_factory,
):
    outgoing, incoming = Ownership(_url(db_factory)), Ownership(_url(db_factory))
    try:
        assert await outgoing.try_acquire()
        assert not await incoming.try_acquire()

        await outgoing.release()
        await asyncio.wait_for(incoming.acquire(), timeout=5)
        assert incoming.held
    finally:
        await outgoing.release()
        await incoming.release()


@pytest.mark.anyio
async def test_a_backend_that_dies_hands_the_work_over_too(db_factory):
    """No goodbye: its database connection is simply gone, as when the
    container is killed."""
    dying, incoming = Ownership(_url(db_factory)), Ownership(_url(db_factory))
    try:
        assert await dying.try_acquire()
        async with db_factory() as session:
            await session.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_locks"
                    " WHERE locktype = 'advisory' AND pid <> pg_backend_pid()"
                )
            )
        await asyncio.wait_for(incoming.acquire(), timeout=5)
        assert not await dying.still_held()
    finally:
        await dying.release()
        await incoming.release()


class _Chat:
    """What the runner needs of a ChatService to run a turn: where its turn
    intervals live, and the turn itself."""

    def __init__(self, session_factory, turn) -> None:
        self.session_factory = session_factory
        self._turn = turn
        self.started = 0
        self.events: list[str] = []

    async def post_system_event(self, topic_id, text, *args, **kwargs):
        self.events.append(text)
        return None

    async def recover_native_tools(self, topic_id):
        return False

    async def converse(self, **kwargs):
        self.started += 1
        async for frame in self._turn():
            yield frame


async def _answers():
    yield {"type": "done"}


@pytest.mark.anyio
async def test_a_message_waits_for_the_takeover_and_is_then_answered(db_factory):
    runner = AgentWorkRunner(InProcessBroker())
    chat = _Chat(db_factory, _answers)
    runner.hold_turns()

    runner.submit(
        chat,
        await a_topic(db_factory),
        author="u",
        content="fix the login page",
        addressed=addressed_to_agent("cheese-seat"),
    )
    await asyncio.sleep(0.5)
    assert chat.started == 0

    runner.start_turns()
    await _until(lambda: chat.started == 1)
    await runner.drain()


@pytest.mark.anyio
async def test_letting_go_leaves_a_delivered_turn_open_for_the_next_backend(
    db_factory,
):
    async def delivered_and_working():
        yield {"type": "prompt_delivered"}
        await asyncio.Event().wait()

    topic = await a_topic(db_factory)
    runner = AgentWorkRunner(InProcessBroker())
    runner.submit(
        _Chat(db_factory, delivered_and_working),
        topic,
        author="u",
        content="fix the login page",
        addressed=addressed_to_agent("cheese-seat"),
    )
    await _until_open(db_factory, 1)
    assert await runner.settle_deliveries(timeout_s=5) == set()

    runner.hold_turns()
    await runner.let_go()

    assert runner.active_work_count() == 0
    assert len(await open_turn_ids(db_factory)) == 1


@pytest.mark.anyio
async def test_a_send_cut_off_by_letting_go_never_arrives_afterwards(db_factory):
    arrived: list[str] = []

    async def still_sending():
        await asyncio.sleep(0.5)
        arrived.append("the prompt")
        yield {"type": "prompt_delivered"}

    runner = AgentWorkRunner(InProcessBroker())
    runner.submit(
        _Chat(db_factory, still_sending),
        await a_topic(db_factory),
        author="u",
        content="fix the login page",
        addressed=addressed_to_agent("cheese-seat"),
    )
    await _until_open(db_factory, 1)
    assert len(await runner.settle_deliveries(timeout_s=0.1)) == 1

    runner.hold_turns()
    await runner.let_go()
    await asyncio.sleep(1)

    assert arrived == []
    # Still open: the incoming backend re-sends it, as a turn nobody heard.
    assert len(await open_turn_ids(db_factory)) == 1


def _works_on_it(channel: Scripted, topic: uuid.UUID, prompt: str) -> None:
    channel.starts(topic)
    channel.acknowledges(topic, prompt)


def _said(events) -> list[str]:
    return [
        event.text
        for _, event in events
        if not isinstance(event, AgentResult) and getattr(event, "text", None)
    ]


@pytest.mark.anyio
async def test_session_output_lands_once_through_the_backend_that_took_over():
    room = Room(Scripted(_works_on_it))
    incoming = ClaudeCodeRuntime(room.channel)
    landed_by_incoming: list = []

    async def consume(_project, _topic, work, event, _eid, _seen, _unsolicited):
        landed_by_incoming.append((work, event))

    incoming.bind_events(consume)
    try:
        await room.send("fix the login page")
        await _until(lambda: room.receipts == ["fix the login page"])

        await room.runtime.stop_listening()
        room.channel.says(room.topic, "the login page is fixed")
        # Longer than the slowest a listening backend waits between reads.
        await asyncio.sleep(1.5)
        assert "the login page is fixed" not in _said(room.events)

        recovered = await incoming.recover()
        assert [ref.topic_id for ref in recovered] == [room.topic]
        await incoming.replay(recovered[0], known_texts=set())
        await _until(lambda: "the login page is fixed" in _said(landed_by_incoming))
        await asyncio.sleep(0.3)

        assert _said(landed_by_incoming).count("the login page is fixed") == 1
        assert "the login page is fixed" not in _said(room.events)
    finally:
        await room.close()
        await incoming.stop_listening()
