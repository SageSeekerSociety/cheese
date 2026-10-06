"""The durable input ledger and the seat owner transition (FB-56).

One platform *input* — a single submission of a prompt to a session — gets a
ledger row before it is sent. A high-entropy nonce planted in the prompt
text is the only thing that later binds the native user entry back to the
input: not position, not the pre-RPC owner stamp (falsified), not the text
of anyone else's prompt.

The rules this module is the whole of:

- nonce ↔ input is 1:1 for the input's life; a retry of the SAME input
  reuses the row (and the nonce), a new input mints a new one.
- delivery and binding are two independent, monotone facts. Delivered never
  demotes bound; bound never rolls back to sent.
- binding happens exactly once, when the native user entry carrying the
  nonce is read. Zero hits or several (a fork copied the entry) are UNKNOWN,
  never a guess; a different entry re-presenting an already-bound nonce does
  not advance anything; a same-epoch, same-entry replay is an idempotent
  no-op.
- the seat owner moves in one transaction with a full expected-CAS: the
  expected owner, generation and head must still be what was read, or the
  transaction aborts and is re-read. A closed owner keeps its head — the
  high-water is a compare baseline, not a liveness claim — so an old or
  replayed callback can never roll it back. A commit that landed while the
  mirror cursor did not is recognized by the CAS failing with the new head
  already in place, and processing advances normally instead of looping on
  pos <= head.
- retiring a work is writing its `stopped_at`, nothing else: never a
  completion, never `_close_hook_work`, never an AgentResult.
"""

import logging
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.agent.models import Base
from app.domain.agent.nonce import new_nonce, nonce_in
from app.domain.common import Uuid

logger = logging.getLogger(__name__)

__all__ = [
    "new_nonce",
    "nonce_in",
    "AgentTurnInput",
    "AgentSeatOwner",
    "Transition",
    "record_input",
    "mark_delivered",
    "mark_session",
    "bind",
    "transition",
]


class AgentTurnInput(Base):
    """The durable ledger of one input (migration d5e8a1c4b790)."""

    __tablename__ = "agent_turn_inputs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    turn_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    nonce: Mapped[str] = mapped_column(String(32), unique=True)
    harness: Mapped[str] = mapped_column(String(16))
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    journal_generation: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="sent")
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    bound_entry_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bound_pos: Mapped[int | None] = mapped_column(nullable=True)
    bound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AgentSeatOwner(Base):
    """The owner and high-water of one seat in one journal generation."""

    __tablename__ = "agent_seat_owner"

    topic_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    agent_handle: Mapped[str] = mapped_column(String(64), primary_key=True)
    journal_generation: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_turn_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    owner_input_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    head_pos: Mapped[int] = mapped_column(default=0)
    head_entry_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The predecessor this owner retired at its takeover (migration
    # e6f9b2d5c801): the cleanup's exact target on any replay.
    retired_turn_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Transition:
    """The outcome of feeding one bound entry to the owner state."""

    def __init__(
        self,
        *,
        kind: str,
        retired_turn_id: uuid.UUID | None = None,
        owner_turn_id: uuid.UUID | None = None,
        reason: str = "",
    ) -> None:
        self.kind = kind  # "receipt" | "takeover" | "noop" | "unknown"
        self.retired_turn_id = retired_turn_id
        self.owner_turn_id = owner_turn_id
        self.reason = reason


async def record_input(
    db: AsyncSession,
    *,
    turn_id: uuid.UUID,
    nonce: str,
    harness: str,
    at: datetime,
) -> AgentTurnInput:
    """Write the durable ledger row BEFORE the input is sent."""
    row = AgentTurnInput(
        id=uuid.uuid4(),
        turn_id=turn_id,
        nonce=nonce,
        harness=harness,
        state="sent",
        sent_at=at,
    )
    db.add(row)
    await db.flush()
    return row


async def mark_delivered(db: AsyncSession, *, nonce: str, at: datetime) -> None:
    """The transport accepted the input. Monotone: never demotes bound — and
    the delivery is a fact worth keeping even then (an echo can land before
    the acknowledgement), so a bound row still records ``delivered_at``."""
    await db.execute(
        update(AgentTurnInput)
        .where(AgentTurnInput.nonce == nonce)
        .where(AgentTurnInput.state.in_(["sent", "delivered"]))
        .values(state="delivered", delivered_at=at)
    )
    await db.execute(
        update(AgentTurnInput)
        .where(AgentTurnInput.nonce == nonce)
        .where(AgentTurnInput.state == "bound")
        .where(AgentTurnInput.delivered_at.is_(None))
        .values(delivered_at=at)
    )


async def mark_session(
    db: AsyncSession,
    *,
    nonce: str,
    session_id: str,
    generation: uuid.UUID,
) -> None:
    """The session the input went to, once the session itself says so."""
    await db.execute(
        update(AgentTurnInput)
        .where(AgentTurnInput.nonce == nonce)
        .values(session_id=session_id, journal_generation=generation)
    )


async def mark_delivered_for_turn(
    db: AsyncSession, *, turn_id: uuid.UUID, at: datetime
) -> None:
    """The transport accepted this turn's outstanding input(s).

    Same monotone rule as :func:`mark_delivered`: a bound input is never
    demoted.
    """
    await db.execute(
        update(AgentTurnInput)
        .where(AgentTurnInput.turn_id == turn_id)
        .where(AgentTurnInput.state.in_(["sent", "delivered"]))
        .values(state="delivered", delivered_at=at)
    )
    await db.execute(
        update(AgentTurnInput)
        .where(AgentTurnInput.turn_id == turn_id)
        .where(AgentTurnInput.state == "bound")
        .where(AgentTurnInput.delivered_at.is_(None))
        .values(delivered_at=at)
    )


async def mark_session_for_turn(
    db: AsyncSession, *, turn_id: uuid.UUID, session_id: str
) -> None:
    """The native session this turn's outstanding input(s) went to — stamped
    on the turn's own row too (FB-56 legacy③): it is the identity a
    termination is matched by."""
    from app.domain.agent.models import AgentTurn

    await db.execute(
        update(AgentTurn)
        .where(AgentTurn.id == turn_id)
        .where(AgentTurn.session_id.is_(None))
        .values(session_id=session_id)
    )
    await db.execute(
        update(AgentTurnInput)
        .where(AgentTurnInput.turn_id == turn_id)
        .where(AgentTurnInput.state.in_(["sent", "delivered"]))
        .values(session_id=session_id)
    )


async def bind(
    db: AsyncSession,
    *,
    nonce: str,
    entry_id: str,
    pos: int,
    generation: uuid.UUID,
    session_id: str | None = None,
    at: datetime,
) -> AgentTurnInput | None:
    """Bind one native entry to its input, idempotently.

    Returns the ledger row when the binding is usable for a transition walk,
    None when it is not: an unknown input; a replay the owner state already
    covered; a different entry re-presenting an already-bound nonce; or a
    source that disagrees with what the ledger already knows — a session id
    or generation already recorded differently is never overwritten (an old
    drain replaying under a new session advances nothing).
    """
    row = await db.scalar(select(AgentTurnInput).where(AgentTurnInput.nonce == nonce))
    if row is None:
        return None
    if (
        session_id is not None
        and row.session_id is not None
        and row.session_id != session_id
    ):
        return None
    if row.journal_generation is not None and row.journal_generation != generation:
        return None
    if row.state == "bound":
        if row.bound_entry_id == entry_id and row.journal_generation == generation:
            return None  # same-epoch, same-entry replay: already done
        return None  # a different entry with a used nonce advances nothing
    row.state = "bound"
    row.bound_entry_id = entry_id
    row.bound_pos = pos
    row.bound_at = at
    row.journal_generation = generation
    if session_id is not None:
        row.session_id = session_id
    await db.flush()
    return row


async def transition(
    db: AsyncSession,
    *,
    topic_id: uuid.UUID,
    agent_handle: str,
    session_id: str | None,
    generation: uuid.UUID,
    work_id: uuid.UUID,
    input_id: uuid.UUID,
    entry_id: str,
    pos: int,
    at: datetime,
) -> Transition:
    """Walk the seat owner for one bound input, in true entry order.

    The owner row is locked (``FOR UPDATE``) for the whole walk, so two
    transitions on one seat serialize instead of interleaving: a
    "read, advance elsewhere, then act" race cannot happen. T == S is a
    receipt — only the head moves. T != S crowns T and retires S's interval
    **inside the same locked transaction**: a failure anywhere rolls the
    crown, the retirement and the binding back together, and the mirror
    cursor that only moves after commit never passed the entry — the replay
    simply does it again. A position at or below the head is a committed
    replay: a no-op, never a loop.
    """
    owner = await db.get(
        AgentSeatOwner,
        (topic_id, agent_handle, generation),
        with_for_update=True,
    )
    expected_head = owner.head_pos if owner is not None else 0
    if owner is not None and pos <= expected_head:
        return Transition(kind="noop", reason="head already past this entry")

    if owner is not None and owner.owner_turn_id == work_id:
        owner.head_pos = pos
        owner.head_entry_id = entry_id
        owner.updated_at = at
        return Transition(kind="receipt", owner_turn_id=work_id)

    retired: uuid.UUID | None = None
    if owner is not None and owner.owner_turn_id is not None:
        retired = owner.owner_turn_id

    if owner is None:
        owner = AgentSeatOwner(
            topic_id=topic_id,
            agent_handle=agent_handle,
            journal_generation=generation,
            session_id=session_id,
            owner_turn_id=work_id,
            owner_input_id=input_id,
            head_pos=pos,
            head_entry_id=entry_id,
            updated_at=at,
        )
        db.add(owner)
        # A concurrent first crown loses the insert race on the primary key;
        # the transaction rolls everything — the binding included — back,
        # and the replay finds the winner's row and classifies itself.
        await db.flush()
    else:
        owner.session_id = session_id
        owner.owner_turn_id = work_id
        owner.owner_input_id = input_id
        owner.head_pos = pos
        owner.head_entry_id = entry_id
        owner.retired_turn_id = retired
        owner.updated_at = at
    if retired is not None:
        await _retire(db, turn_id=retired, at=at)
    return Transition(kind="takeover", retired_turn_id=retired, owner_turn_id=work_id)


async def _retire(db: AsyncSession, *, turn_id: uuid.UUID, at: datetime) -> None:
    """End the interval. `stopped_at` is the whole of it — never a
    completion, never hook-work accounting."""
    from app.domain.agent.models import AgentTurn

    await db.execute(
        update(AgentTurn)
        .where(AgentTurn.id == turn_id)
        .where(AgentTurn.stopped_at.is_(None))
        .values(stopped_at=at)
    )


async def ensure_interval_and_input(
    db: AsyncSession,
    *,
    topic_id: uuid.UUID,
    turn_id: uuid.UUID,
    author: str,
    content: str,
    is_resume: bool,
    continuation_id: uuid.UUID | None,
    harness: str,
    nonce: str,
    at: datetime,
) -> None:
    """The parent interval and this input's ledger row, flushed parent-first
    in the caller's one commit (the ledger's FK names the interval).

    The interval opens idempotently (ON CONFLICT DO NOTHING): a turn the work
    runner already opened keeps the runner's own row — its ``resendable``
    was computed with the resume reason this call does not have. A turn
    driven straight into converse gets the same honest interval a
    runner-driven one has: the sweep can find its corpse, and a Stop can
    close it once delivered.
    """
    from app.domain.agent.repositories import AgentTurnRepository

    await AgentTurnRepository(db).open(
        turn_id=turn_id,
        conversation_id=topic_id,
        continuation_id=continuation_id or turn_id,
        author=author,
        content=content,
        is_resume=is_resume,
        resendable=bool(content.strip()) and not is_resume,
        started_at=at,
        exists_ok=True,
    )
    await record_input(db, turn_id=turn_id, nonce=nonce, harness=harness, at=at)


async def stamp_delivered(
    db: AsyncSession, *, turn_id: uuid.UUID, at: datetime
) -> None:
    """The transport accepted this turn's write: the parent interval and its
    input(s) record delivery, monotone (the first stamp wins; a bound input
    is never demoted). Stamped at the source, so a converse driven without
    the work runner leaves the same fact a runner-driven one does.
    """
    from app.domain.agent.repositories import AgentTurnRepository

    await AgentTurnRepository(db).mark_delivered(turn_id, at)
    await mark_delivered_for_turn(db, turn_id=turn_id, at=at)


async def retire_failed(
    session_factory, *, topic_id: uuid.UUID, turn_id: uuid.UUID, at: datetime
) -> None:
    """Own the session for a pre-handoff failure's exact-id retirement,
    logging rather than raising: the turn already failed, and a bookkeeping
    error must not be reported as a second one.
    """
    try:
        async with session_factory() as session:
            await retire_failed_interval(session, turn_id=turn_id, at=at)
            await session.commit()
    except Exception:  # noqa: BLE001 — the turn already failed
        logger.exception(
            "could not close the failed turn's interval (topic=%s, turn=%s)",
            topic_id,
            turn_id,
        )


async def retire_failed_interval(
    db: AsyncSession, *, turn_id: uuid.UUID, at: datetime
) -> None:
    """A write that never reached the transport retires its OWN interval, by
    id, delivered or not — the Stop consumer's ``close_one`` rightly refuses
    an undelivered row, and the turn's own coroutine is the only other
    closer. Exactly this id: a failure retires nobody else's interval.
    """
    from app.domain.agent.repositories import AgentTurnRepository

    await AgentTurnRepository(db).close([turn_id], at)


async def open_interval_with_input(
    session_factory,
    *,
    topic_id: uuid.UUID,
    turn_id: uuid.UUID,
    author: str,
    content: str,
    is_resume: bool,
    continuation_id: uuid.UUID | None,
    harness: str,
    nonce: str,
    at: datetime,
) -> None:
    """Own the session for :func:`ensure_interval_and_input`: parent and
    ledger row land in one commit, opened idempotently — a runner-opened
    interval keeps the runner's own fields.
    """
    async with session_factory() as session:
        await ensure_interval_and_input(
            session,
            topic_id=topic_id,
            turn_id=turn_id,
            author=author,
            content=content,
            is_resume=is_resume,
            continuation_id=continuation_id,
            harness=harness,
            nonce=nonce,
            at=at,
        )
        await session.commit()


async def stamp_delivery_fact(
    session_factory, *, turn_id: uuid.UUID, at: datetime
) -> None:
    """Record that the transport accepted this turn's prompt. Swallows its
    own failure, unlike opening the interval: this runs mid-turn on a turn
    that is working, and losing the stamp costs at most one duplicate
    re-send if the process then dies, while raising here would kill the
    live turn to protect it from a hypothetical one — a trade nobody would
    make deliberately.
    """
    try:
        async with session_factory() as session:
            await stamp_delivered(session, turn_id=turn_id, at=at)
            await session.commit()
    except Exception:  # noqa: BLE001 — bookkeeping must not kill a working turn
        logger.exception("could not stamp delivery for turn %s", turn_id)
