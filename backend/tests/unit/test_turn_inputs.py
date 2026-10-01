"""The input ledger and the seat-owner transition rules (FB-56).

Each rule here is one the review loop pinned with real sources: binding is
unique-or-unknown, delivery and binding are monotone in both directions, a
takeover retires exactly the predecessor's `stopped_at` in one transaction,
and a replayed or already-committed entry can never roll the head back.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.domain.agent.turn_inputs import (
    AgentSeatOwner,
    AgentTurnInput,
    bind,
    mark_delivered,
    record_input,
    transition,
)
from tests.turn_log import a_topic, open_turn, turn_row

_NOW = datetime(2026, 10, 1, tzinfo=UTC)


async def _input(factory, turn_id, *, nonce, state="sent"):
    async with factory() as session:
        row = await record_input(
            session,
            turn_id=turn_id,
            nonce=nonce,
            harness="pi",
            at=_NOW,
        )
        if state == "bound":
            row.state = "bound"
            row.bound_entry_id = "e0"
            row.bound_pos = 1
            row.bound_at = _NOW
        await session.commit()
        return row.id


async def _ledger(factory, nonce):
    async with factory() as session:
        return await session.scalar(
            select(AgentTurnInput).where(AgentTurnInput.nonce == nonce)
        )


async def _owner(factory, topic_id, agent, generation):
    async with factory() as session:
        return await session.get(AgentSeatOwner, (topic_id, agent, generation))


@pytest.mark.anyio
async def test_a_bound_input_cannot_be_demoted_by_delivery(db_factory):
    """delivery and binding are two independent monotone facts: an input
    already bound stays bound when a late delivery stamp lands."""
    topic = await a_topic(db_factory)
    turn_id = await open_turn(db_factory, topic, delivered=True)
    nonce = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    await _input(db_factory, turn_id, nonce=nonce, state="bound")

    async with db_factory() as session:
        await mark_delivered(session, nonce=nonce, at=_NOW)
        await session.commit()

    row = await _ledger(db_factory, nonce)
    assert row.state == "bound", "bound 不被 delivered 降级"


@pytest.mark.anyio
async def test_a_replayed_entry_advances_nothing(db_factory):
    """A different entry re-presenting an already-bound nonce advances
    nothing; the same entry in the same generation is an idempotent no-op."""
    topic = await a_topic(db_factory)
    turn_id = await open_turn(db_factory, topic, delivered=True)
    nonce = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    await _input(db_factory, turn_id, nonce=nonce, state="bound")

    generation = uuid.uuid4()
    async with db_factory() as session:
        replayed = await bind(
            session,
            nonce=nonce,
            entry_id="e0",
            pos=1,
            generation=generation,
            at=_NOW,
        )
        assert replayed is None, "同 entry 同 epoch 重放幂等"
        foreign = await bind(
            session,
            nonce=nonce,
            entry_id="e9",
            pos=9,
            generation=generation,
            at=_NOW,
        )
        assert foreign is None, "异 entry 复用已 bound nonce 不推进"

    row = await _ledger(db_factory, nonce)
    assert row.bound_entry_id == "e0" and row.bound_pos == 1


@pytest.mark.anyio
async def test_an_unknown_nonce_binds_nothing(db_factory):
    async with db_factory() as session:
        row = await bind(
            session,
            nonce=f"⟪w:{uuid.uuid4().hex[:24]}⟫",
            entry_id="e1",
            pos=1,
            generation=uuid.uuid4(),
            at=_NOW,
        )
        assert row is None, "台账查不到的 nonce 不绑定、不猜"


@pytest.mark.anyio
async def test_a_takeover_retires_exactly_the_predecessor(db_factory):
    """T != S: S's interval ends — `stopped_at`, and nothing else — while T
    is crowned and the head advances, in one transaction."""
    topic = await a_topic(db_factory)
    old_turn = await open_turn(db_factory, topic, delivered=True)
    new_turn = await open_turn(db_factory, topic, delivered=True)
    generation = uuid.uuid4()
    old_nonce = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    new_nonce = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    await _input(db_factory, old_turn, nonce=old_nonce)
    new_input = await _input(db_factory, new_turn, nonce=new_nonce)

    async with db_factory() as session:
        # The old work binds first and becomes the owner.
        first = await bind(
            session,
            nonce=old_nonce,
            entry_id="e1",
            pos=1,
            generation=generation,
            at=_NOW,
        )
        assert first is not None
        outcome = await transition(
            session,
            topic_id=topic,
            agent_handle="cheese",
            session_id="sess-1",
            generation=generation,
            work_id=old_turn,
            input_id=first.id,
            entry_id="e1",
            pos=1,
            at=_NOW,
        )
        assert outcome.kind == "takeover" and outcome.retired_turn_id is None

        # The new work binds: its transition retires the old one's interval.
        second = await bind(
            session,
            nonce=new_nonce,
            entry_id="e2",
            pos=2,
            generation=generation,
            at=_NOW,
        )
        assert second is not None
        outcome = await transition(
            session,
            topic_id=topic,
            agent_handle="cheese",
            session_id="sess-1",
            generation=generation,
            work_id=new_turn,
            input_id=second.id,
            entry_id="e2",
            pos=2,
            at=_NOW,
        )
        await session.commit()

    assert outcome.kind == "takeover"
    assert outcome.retired_turn_id == old_turn
    assert outcome.owner_turn_id == new_turn

    old_row = await turn_row(db_factory, old_turn)
    new_row = await turn_row(db_factory, new_turn)
    assert old_row.stopped_at is not None, "前任 interval 退休"
    assert new_row.stopped_at is None, "接任者保持 open"

    owner = await _owner(db_factory, topic, "cheese", generation)
    assert owner.owner_turn_id == new_turn
    assert owner.owner_input_id == new_input
    assert owner.head_pos == 2


@pytest.mark.anyio
async def test_a_receipt_advances_only_the_head(db_factory):
    """T == S is a receipt for the standing owner: the head moves, nothing
    is retired."""
    topic = await a_topic(db_factory)
    turn_id = await open_turn(db_factory, topic, delivered=True)
    generation = uuid.uuid4()
    nonce = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    input_id = await _input(db_factory, turn_id, nonce=nonce)

    async with db_factory() as session:
        row = await bind(
            session,
            nonce=nonce,
            entry_id="e1",
            pos=1,
            generation=generation,
            at=_NOW,
        )
        await transition(
            session,
            topic_id=topic,
            agent_handle="cheese",
            session_id="sess-1",
            generation=generation,
            work_id=turn_id,
            input_id=row.id,
            entry_id="e1",
            pos=1,
            at=_NOW,
        )
        # A second input of the SAME work binds: receipt, not a takeover.
        same = await record_input(
            session,
            turn_id=turn_id,
            nonce=f"⟪w:{uuid.uuid4().hex[:24]}⟫",
            harness="pi",
            at=_NOW,
        )
        await session.flush()
        bound = await bind(
            session,
            nonce=same.nonce,
            entry_id="e2",
            pos=2,
            generation=generation,
            at=_NOW,
        )
        outcome = await transition(
            session,
            topic_id=topic,
            agent_handle="cheese",
            session_id="sess-1",
            generation=generation,
            work_id=turn_id,
            input_id=bound.id,
            entry_id="e2",
            pos=2,
            at=_NOW,
        )
        await session.commit()

    assert outcome.kind == "receipt"
    assert outcome.retired_turn_id is None
    row = await turn_row(db_factory, turn_id)
    assert row.stopped_at is None, "同 work 输入不退休自己"
    owner = await _owner(db_factory, topic, "cheese", generation)
    assert owner.head_pos == 2
    assert owner.owner_turn_id == turn_id
    assert owner.owner_input_id == input_id, "owner 归属不被同 work receipt 改写"


@pytest.mark.anyio
async def test_an_entry_at_or_below_the_head_is_a_noop(db_factory):
    """pos <= head means the work is already committed: a replay is
    recognized and processing advances instead of re-reading forever."""
    topic = await a_topic(db_factory)
    turn_id = await open_turn(db_factory, topic, delivered=True)
    generation = uuid.uuid4()
    nonce = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    await _input(db_factory, turn_id, nonce=nonce)

    async with db_factory() as session:
        row = await bind(
            session,
            nonce=nonce,
            entry_id="e5",
            pos=5,
            generation=generation,
            at=_NOW,
        )
        await transition(
            session,
            topic_id=topic,
            agent_handle="cheese",
            session_id="sess-1",
            generation=generation,
            work_id=turn_id,
            input_id=row.id,
            entry_id="e5",
            pos=5,
            at=_NOW,
        )
        # A later replay of an EARLIER position must not walk anything back.
        outcome = await transition(
            session,
            topic_id=topic,
            agent_handle="cheese",
            session_id="sess-1",
            generation=generation,
            work_id=turn_id,
            input_id=row.id,
            entry_id="e3",
            pos=3,
            at=_NOW,
        )
        await session.commit()

    assert outcome.kind == "noop"
    owner = await _owner(db_factory, topic, "cheese", generation)
    assert owner.head_pos == 5, "pos<=head 不回退 high-water"


@pytest.mark.anyio
async def test_a_bound_input_still_records_its_delivery(db_factory):
    """echo-before-ack: an input already bound keeps its state, and the late
    delivery stamp still lands as a fact."""
    topic = await a_topic(db_factory)
    turn_id = await open_turn(db_factory, topic, delivered=True)
    nonce = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    await _input(db_factory, turn_id, nonce=nonce, state="bound")

    async with db_factory() as session:
        await mark_delivered(session, nonce=nonce, at=_NOW)
        await session.commit()

    row = await _ledger(db_factory, nonce)
    assert row.state == "bound"
    assert row.delivered_at is not None, "bound 后 delivery 事实不丢"


@pytest.mark.anyio
async def test_a_stale_source_never_binds(db_factory):
    """A session or generation the ledger already knows otherwise is not
    overwritten: an old drain replaying under a new session advances
    nothing; the matching source binds."""
    topic = await a_topic(db_factory)
    turn_id = await open_turn(db_factory, topic, delivered=True)
    nonce = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    generation = uuid.uuid4()
    async with db_factory() as session:
        row = await record_input(
            session, turn_id=turn_id, nonce=nonce, harness="pi", at=_NOW
        )
        row.session_id = "sess-new"
        row.journal_generation = generation
        await session.commit()

    async with db_factory() as session:
        stale_sid = await bind(
            session,
            nonce=nonce,
            entry_id="e1",
            pos=1,
            generation=generation,
            session_id="sess-old",
            at=_NOW,
        )
        assert stale_sid is None, "sid 不符不绑"
        stale_gen = await bind(
            session,
            nonce=nonce,
            entry_id="e1",
            pos=1,
            generation=uuid.uuid4(),
            session_id="sess-new",
            at=_NOW,
        )
        assert stale_gen is None, "generation 不符不绑"
        fresh = await bind(
            session,
            nonce=nonce,
            entry_id="e1",
            pos=1,
            generation=generation,
            session_id="sess-new",
            at=_NOW,
        )
        assert fresh is not None, "来源相符才绑"
        await session.commit()

    row = await _ledger(db_factory, nonce)
    assert row.session_id == "sess-new"
    assert row.journal_generation == generation
    assert row.bound_entry_id == "e1"


@pytest.mark.anyio
async def test_a_losing_first_crown_rolls_back_everything(db_factory):
    """Two uncommitted transactions both find no owner row: the loser's
    insert conflicts on the primary key the moment the winner commits, and
    its whole transaction — the binding included — rolls back, ready to be
    done again."""
    import asyncio

    import sqlalchemy.exc

    topic = await a_topic(db_factory)
    turn_a = await open_turn(db_factory, topic, delivered=True)
    turn_b = await open_turn(db_factory, topic, delivered=True)
    generation = uuid.uuid4()
    nonce_a = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    nonce_b = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    await _input(db_factory, turn_a, nonce=nonce_a)
    await _input(db_factory, turn_b, nonce=nonce_b)

    session_a = await db_factory().__aenter__()
    session_b = await db_factory().__aenter__()
    try:
        row_a = await bind(
            session_a,
            nonce=nonce_a,
            entry_id="e1",
            pos=1,
            generation=generation,
            at=_NOW,
        )
        row_b = await bind(
            session_b,
            nonce=nonce_b,
            entry_id="e2",
            pos=2,
            generation=generation,
            at=_NOW,
        )
        # A crowns (uncommitted): its insert exists but is invisible to B
        # until commit. B's competing insert blocks on the key.
        await transition(
            session_a,
            topic_id=topic,
            agent_handle="cheese",
            session_id="sess-1",
            generation=generation,
            work_id=turn_a,
            input_id=row_a.id,
            entry_id="e1",
            pos=1,
            at=_NOW,
        )
        loser = asyncio.create_task(
            transition(
                session_b,
                topic_id=topic,
                agent_handle="cheese",
                session_id="sess-1",
                generation=generation,
                work_id=turn_b,
                input_id=row_b.id,
                entry_id="e2",
                pos=2,
                at=_NOW,
            )
        )
        await asyncio.sleep(0.5)  # let B reach its blocked insert
        await session_a.commit()
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            await asyncio.wait_for(asyncio.shield(loser), timeout=30)
        await session_b.rollback()

        row_b = await _ledger(db_factory, nonce_b)
        assert row_b.state != "bound", "败者事务整体回滚：绑定一起回"
        row_a = await _ledger(db_factory, nonce_a)
        assert row_a.state == "bound"

        # The replay binds and classifies itself against the winner's
        # owner row: a legitimate sequential takeover.
        async with db_factory() as session:
            retry = await bind(
                session,
                nonce=nonce_b,
                entry_id="e2",
                pos=2,
                generation=generation,
                at=_NOW,
            )
            assert retry is not None, "回滚后重放可以重新绑定"
            outcome = await transition(
                session,
                topic_id=topic,
                agent_handle="cheese",
                session_id="sess-1",
                generation=generation,
                work_id=turn_b,
                input_id=retry.id,
                entry_id="e2",
                pos=2,
                at=_NOW,
            )
            await session.commit()
        assert outcome.kind == "takeover"
        assert outcome.retired_turn_id == turn_a
    finally:
        await session_a.close()
        await session_b.close()


@pytest.mark.anyio
async def test_a_blocked_transition_reads_the_new_head_and_stops(db_factory):
    """A transition whose lock wait spans another crown re-reads the row
    when it unblocks: an entry at or below the NEW head is a no-op with no
    side effects, never a stale retirement."""
    import asyncio

    topic = await a_topic(db_factory)
    turn_a = await open_turn(db_factory, topic, delivered=True)
    turn_b = await open_turn(db_factory, topic, delivered=True)
    turn_c = await open_turn(db_factory, topic, delivered=True)
    generation = uuid.uuid4()
    nonce_a = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    nonce_b = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    nonce_c = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    await _input(db_factory, turn_a, nonce=nonce_a)
    await _input(db_factory, turn_b, nonce=nonce_b)
    await _input(db_factory, turn_c, nonce=nonce_c)

    # The first crown is committed: turn_a owns the seat at head 2.
    async with db_factory() as session:
        row_a = await bind(
            session,
            nonce=nonce_a,
            entry_id="e2",
            pos=2,
            generation=generation,
            at=_NOW,
        )
        await transition(
            session,
            topic_id=topic,
            agent_handle="cheese",
            session_id="sess-1",
            generation=generation,
            work_id=turn_a,
            input_id=row_a.id,
            entry_id="e2",
            pos=2,
            at=_NOW,
        )
        await session.commit()

    session_b = await db_factory().__aenter__()
    session_c = await db_factory().__aenter__()
    try:
        # B locks the owner row for a takeover to head 4, uncommitted.
        row_b = await bind(
            session_b,
            nonce=nonce_b,
            entry_id="e4",
            pos=4,
            generation=generation,
            at=_NOW,
        )
        await transition(
            session_b,
            topic_id=topic,
            agent_handle="cheese",
            session_id="sess-1",
            generation=generation,
            work_id=turn_b,
            input_id=row_b.id,
            entry_id="e4",
            pos=4,
            at=_NOW,
        )
        # C's entry sits below B's new head: its lock request blocks behind
        # B, and when B commits it re-reads head=4 and stops.
        row_c = await bind(
            session_c,
            nonce=nonce_c,
            entry_id="e3",
            pos=3,
            generation=generation,
            at=_NOW,
        )
        blocked = asyncio.create_task(
            transition(
                session_c,
                topic_id=topic,
                agent_handle="cheese",
                session_id="sess-1",
                generation=generation,
                work_id=turn_c,
                input_id=row_c.id,
                entry_id="e3",
                pos=3,
                at=_NOW,
            )
        )
        await asyncio.sleep(0.5)  # let C block on the owner lock
        await session_b.commit()
        outcome = await asyncio.wait_for(blocked, timeout=30)
        await session_c.commit()

        assert outcome.kind == "noop", "解锁后重读到新 head：pos<=head 成 noop"
        assert outcome.retired_turn_id is None
        row_a_turn = await turn_row(db_factory, turn_a)
        row_c_turn = await turn_row(db_factory, turn_c)
        assert row_a_turn.stopped_at is not None, "B 的接管退了 A，且只一次"
        assert row_c_turn.stopped_at is None, "noop 无副作用：C 的行不被误退"
        owner = await _owner(db_factory, topic, "cheese", generation)
        assert owner.owner_turn_id == turn_b
        assert owner.head_pos == 4
    finally:
        await session_b.close()
        await session_c.close()
