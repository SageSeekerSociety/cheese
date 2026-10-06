"""A Stop closes its own work's interval, never a neighbour's (FB-56).

The Stop a harness sends ends one piece of work: the session that finished.
Today the close it triggers is topic-wide (`close_for_topic`), so one
teammate's Stop settles every delivered open interval in the room — a second
teammate still working finds its turn closed from under it.

These tests encode the rule the fix must hold, at the lowest level that is
honest: real rows on real Postgres, driven through the same repository close
the Stop path uses. Each one names the stamped work id the Stop carries and
asserts only that id's row closes. On current code every one of them is RED,
because the close entry the Stop path uses
(`AgentTurnRepository.close_for_topic`) does not look at the id at all.

The call marked ``close_entry`` is the one place the fix swaps for the scoped
entry (`_close_open_turns(topic_id, turn_id)` down to a by-id close); the
assertions — the rule itself — do not change when it does.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.domain.agent.models import AgentTurn
from app.domain.agent.repositories import AgentTurnRepository
from tests.integration.conftest import post_project


def _room(client) -> uuid.UUID:
    project = post_project(client, {"name": "StopScope"}, owner="alice")
    return uuid.UUID(project.json()["data"]["root_topic_id"])


def _open_turn(session, room, *, turn_id, continuation_id, agent, delivered=True):
    asyncio.get_event_loop()
    repo = AgentTurnRepository(session)
    return repo.open(
        turn_id=turn_id,
        conversation_id=room,
        continuation_id=continuation_id,
        author=agent,
        content="work",
        is_resume=False,
        resendable=True,
        started_at=datetime.now(UTC),
        delivered_at=datetime.now(UTC) if delivered else None,
        agent_handle=agent,
    )


async def _close_entry(session, room, stop_work_id):
    """The close the Stop path performs, given the id the Stop stamped.

    The fix: `close_one` owns exactly ``stop_work_id`` — the row it names,
    or nothing when the owner cannot be located.
    """
    closed = await AgentTurnRepository(session).close_one(
        room, stop_work_id, datetime.now(UTC)
    )
    await session.commit()
    return closed


async def _states(session, room):
    rows = (
        await session.execute(
            select(AgentTurn.id, AgentTurn.stopped_at, AgentTurn.agent_handle).where(
                AgentTurn.conversation_id == room
            )
        )
    ).all()
    return {row.id: row.stopped_at for row in rows}


def test_a_stop_closes_only_its_own_seats_turn(client):
    """R1: two teammates working in one room; 甲's Stop must leave 乙's turn
    open. Current code closes both — red."""
    room = _room(client)
    turn_a, turn_b = uuid.uuid4(), uuid.uuid4()

    async def run():
        async with client.test_factory() as session:
            await _open_turn(
                session,
                room,
                turn_id=turn_a,
                continuation_id=turn_a,
                agent="cheese-a",
            )
            await _open_turn(
                session,
                room,
                turn_id=turn_b,
                continuation_id=turn_b,
                agent="second-b",
            )
            await session.commit()
            await _close_entry(session, room, stop_work_id=turn_a)
            return await _states(session, room)

    states = asyncio.run(run())
    assert states[turn_a] is not None, "甲's own turn should be closed"
    assert states[turn_b] is None, "乙's turn must still be open (FB-56)"


def test_an_old_attempts_stop_leaves_the_new_attempt_open(client):
    """R2′: one chain, two attempts — the old attempt's late Stop carries the
    OLD id and must close only the old row, though both rows share the
    continuation. Current code closes both — red."""
    room = _room(client)
    chain = uuid.uuid4()
    old_attempt, new_attempt = uuid.uuid4(), uuid.uuid4()

    async def run():
        async with client.test_factory() as session:
            await _open_turn(
                session,
                room,
                turn_id=old_attempt,
                continuation_id=chain,
                agent="cheese-a",
            )
            await _open_turn(
                session,
                room,
                turn_id=new_attempt,
                continuation_id=chain,
                agent="cheese-a",
            )
            await session.commit()
            await _close_entry(session, room, stop_work_id=old_attempt)
            return await _states(session, room)

    states = asyncio.run(run())
    assert states[old_attempt] is not None, "the old attempt's row closes"
    assert states[new_attempt] is None, "the new attempt must stay open (FB-56)"


def test_a_replayed_stop_closes_nothing(client):
    """R4: the row the replayed Stop names is already closed; another
    teammate's delivered turn is open. Nothing may close. Current code closes
    the open one — red."""
    room = _room(client)
    done_turn, live_turn = uuid.uuid4(), uuid.uuid4()

    async def run():
        async with client.test_factory() as session:
            await _open_turn(
                session,
                room,
                turn_id=done_turn,
                continuation_id=done_turn,
                agent="cheese-a",
            )
            await _open_turn(
                session,
                room,
                turn_id=live_turn,
                continuation_id=live_turn,
                agent="second-b",
            )
            await session.commit()
            # The first Stop is legitimate: it closes its own row.
            await _close_entry_for_one(session, room, done_turn)
            # The replay arrives later, naming the same (now closed) work.
            closed = await _close_entry(session, room, stop_work_id=done_turn)
            return closed, await _states(session, room)

    closed, states = asyncio.run(run())
    assert closed == 0, "a replayed Stop settles nothing"
    assert states[live_turn] is None, "the live turn must stay open (FB-56)"


async def _close_entry_for_one(session, room, turn_id):
    """Close exactly one row, the way the turn's own coroutine does by id —
    used to set up the 'already closed' state honestly."""
    await session.execute(
        AgentTurn.__table__.update()
        .where(AgentTurn.id == turn_id)
        .values(stopped_at=datetime.now(UTC))
    )
    await session.commit()


def test_an_unknown_stops_work_id_closes_nothing(client):
    """R5: the stamped id names no row in this room (stale, foreign, or made
    up): the owner cannot be located, so nothing closes and the rows wait for
    reconciliation. Current code closes the open one — red."""
    room = _room(client)
    live_turn = uuid.uuid4()

    async def run():
        async with client.test_factory() as session:
            await _open_turn(
                session,
                room,
                turn_id=live_turn,
                continuation_id=live_turn,
                agent="cheese-a",
            )
            await session.commit()
            closed = await _close_entry(session, room, stop_work_id=uuid.uuid4())
            return closed, await _states(session, room)

    closed, states = asyncio.run(run())
    assert closed == 0, "an unlocatable owner closes nothing"
    assert states[live_turn] is None, "the live turn must stay open (FB-56)"
