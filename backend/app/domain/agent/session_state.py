"""A seat's session as its runner declares it, and the events it applied once.

The runner reports in the lifecycle protocol (`harness/driven/lifecycle.py`).
Here is what the platform keeps of it durably: the worker epoch it issued the
seat's runner (``register``), the worker status the runner last declared, and
how far each registration's events have been applied (``take``). Everything
that changes because of an event is written in the transaction ``take`` runs
in, so an event is applied exactly once whatever is redelivered.
"""

import enum
import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.request_facts import recall, remember
from app.domain.agent.harness import HARNESSES
from app.domain.agent.harness.driven.lifecycle import IDLE, RUNNING, StaleEpoch
from app.domain.agent.models import SessionOffset, SessionState

#: What a session with no place is keyed under (a person's 芝士, a document's):
#: its runner's home is its only identity, so the conversation is named by it.
UNOWNED = uuid.UUID("6f1c9a1e-2b0e-4d3a-9e6f-5f6b0c1d2e3f")


@dataclass(frozen=True)
class SeatKey:
    """Whose session: the conversation, the agent acting in it, the harness."""

    conversation_id: uuid.UUID
    agent_handle: str
    harness: str

    @classmethod
    def unowned(cls, home: str, harness: str) -> "SeatKey":
        return cls(uuid.uuid5(UNOWNED, home), "", harness)


def _key(key: SeatKey):
    return (
        SessionState.conversation_id == key.conversation_id,
        SessionState.agent_handle == key.agent_handle,
        SessionState.harness == key.harness,
    )


async def register(sessions: async_sessionmaker[AsyncSession], key: SeatKey) -> int:
    """Issue the seat's runner a new worker epoch, and return it. Every epoch
    issued before it is stale from this commit on."""
    now = datetime.now(UTC)
    async with sessions() as session:
        epoch = await session.scalar(
            insert(SessionState)
            .values(
                conversation_id=key.conversation_id,
                agent_handle=key.agent_handle,
                harness=key.harness,
                worker_epoch=1,
                worker_status=IDLE,
                changed_at=now,
            )
            .on_conflict_do_update(
                index_elements=["conversation_id", "agent_handle", "harness"],
                set_={"worker_epoch": SessionState.worker_epoch + 1},
            )
            .returning(SessionState.worker_epoch)
        )
        await session.commit()
    assert epoch is not None
    return int(epoch)


class Claim(enum.Enum):
    """What delivering an event again asks of the platform."""

    #: Never applied: its effects are written in this transaction, and what
    #: follows from them is done after it.
    NEW = "new"
    #: Applied, and what follows from it outside the transaction (the room's
    #: books, what is told) was not all done: that is done again.
    UNPROJECTED = "unprojected"
    #: Applied and followed through: nothing is done again.
    DONE = "done"


async def take(
    session: AsyncSession, key: SeatKey, *, worker_epoch: int, epoch: int, seq: int
) -> Claim:
    """Claim one event for this transaction: ``NEW`` when its effects are to
    be applied now, ``UNPROJECTED`` when they were and what follows from them
    was not all done, ``DONE`` when both were.

    Raises ``StaleEpoch`` when the runner that delivered it is not the one the
    seat is registered to now. The seat's row is locked until the transaction
    ends, so a registration cannot slip in between the check and the effects.
    """
    current = await session.scalar(
        select(SessionState.worker_epoch).where(*_key(key)).with_for_update()
    )
    if current != worker_epoch:
        raise StaleEpoch(worker_epoch, current)
    claimed = await session.scalar(
        insert(SessionOffset)
        .values(
            conversation_id=key.conversation_id,
            agent_handle=key.agent_handle,
            harness=key.harness,
            epoch=epoch,
            applied_seq=seq,
        )
        .on_conflict_do_update(
            index_elements=["conversation_id", "agent_handle", "harness", "epoch"],
            set_={"applied_seq": seq},
            where=SessionOffset.applied_seq < seq,
        )
        .returning(SessionOffset.applied_seq)
    )
    if claimed is not None:
        return Claim.NEW
    projected = await session.scalar(
        select(SessionOffset.projected_seq).where(*_offset(key, epoch))
    )
    return Claim.UNPROJECTED if (projected or 0) < seq else Claim.DONE


async def projected(
    sessions: async_sessionmaker[AsyncSession],
    key: SeatKey,
    *,
    epoch: int,
    seq: int,
) -> None:
    """What follows from the event ``(epoch, seq)`` outside the transaction
    that applied it is done: delivered again, it asks nothing more."""
    async with sessions() as session:
        await session.execute(
            update(SessionOffset)
            .where(*_offset(key, epoch))
            .values(projected_seq=func.greatest(SessionOffset.projected_seq, seq))
        )
        await session.commit()


async def result_landed(
    session: AsyncSession, key: SeatKey, *, epoch: int, seq: int
) -> bool:
    """Whether the room already landed the closing result event ``(epoch,
    seq)`` carried: what follows from it is being done again."""
    landed_seq = await session.scalar(
        select(SessionOffset.landed_seq).where(*_offset(key, epoch))
    )
    return (landed_seq or 0) >= seq


async def landed(
    sessions: async_sessionmaker[AsyncSession],
    key: SeatKey,
    *,
    epoch: int,
    seq: int,
) -> None:
    """The room landed the closing result event ``(epoch, seq)`` carried."""
    async with sessions() as session:
        await session.execute(
            update(SessionOffset)
            .where(*_offset(key, epoch))
            .values(landed_seq=func.greatest(SessionOffset.landed_seq, seq))
        )
        await session.commit()


def _offset(key: SeatKey, epoch: int):
    return (
        SessionOffset.conversation_id == key.conversation_id,
        SessionOffset.agent_handle == key.agent_handle,
        SessionOffset.harness == key.harness,
        SessionOffset.epoch == epoch,
    )


async def declare(session: AsyncSession, key: SeatKey, status: str) -> bool:
    """What the runner declared the session to be doing. ``changed_at`` is
    when the platform applied it, on the clock that stamps an input's read
    (`delivery.receipts`): reports apply in the order the runner made them, so
    a read stamped after this was read in what the session did from here on,
    however far the runner's own clock is from the platform's. Says whether
    the status changed."""
    state = await session.scalar(
        select(SessionState).where(*_key(key)).with_for_update()
    )
    if state is None or state.worker_status == status:
        return False
    state.worker_status = status
    state.changed_at = datetime.now(UTC)
    return True


async def exited(
    session: AsyncSession, key: SeatKey, at: datetime, *, epoch: int
) -> bool:
    """The process the runner registered under ``epoch`` ended, as of ``at``.

    Only the seat's current registration's exit ends its session: idle, and
    exited. An exit written under an earlier epoch is of a process a newer
    registration has replaced, whose session may be running now, so it changes
    nothing here; that process's unread inputs are settled by the drops its
    runner reports beside it. Says whether this was the current session's."""
    state = await session.scalar(
        select(SessionState).where(*_key(key)).with_for_update()
    )
    if state is None or state.worker_epoch != epoch:
        return False
    if state.worker_status != IDLE:
        state.worker_status = IDLE
        # The platform's clock, as `declare`'s.
        state.changed_at = datetime.now(UTC)
    state.exited_at = at
    return True


_SESSION_STATE_TABLES = frozenset({"session_states"})


async def running_conversations(
    session: AsyncSession, conversation_ids: Collection[uuid.UUID] | None = None
) -> set[uuid.UUID]:
    """The conversations at least one seat of which has a session its runner
    declared ``running``; of ``conversation_ids`` only, when given."""
    query = select(SessionState.conversation_id).where(
        SessionState.worker_status == RUNNING
    )
    if conversation_ids is not None:
        wanted = set(conversation_ids)
        if not wanted:
            return set()
        if not isinstance(session, AsyncSession):
            query = query.where(SessionState.conversation_id.in_(wanted))
            return set(await session.scalars(query.distinct()))
        if session.autoflush:
            await session.flush()
        known: set[uuid.UUID] = set()
        missing: list[uuid.UUID] = []
        for cid in wanted:
            found, cached = recall(session, ("running_conversation", cid))
            if found:
                if cached:
                    known.add(cid)
            else:
                missing.append(cid)
        if not missing:
            return known
        query = query.where(SessionState.conversation_id.in_(missing))
        found_running = set(await session.scalars(query.distinct()))
        for cid in missing:
            remember(
                session,
                ("running_conversation", cid),
                cid in found_running,
                tables=_SESSION_STATE_TABLES,
            )
        return known | found_running
    return set(await session.scalars(query.distinct()))


async def run_started(
    session: AsyncSession, key: SeatKey, *, epoch: int, seq: int
) -> None:
    """The seat's current process began a run, with the ``running`` event
    ``(epoch, seq)``: the identity its runner names that run by, and that a
    platform tool call made in it carries (`executor_transport.PlatformHost`).
    Recorded for every applied running event, whichever registration
    delivers it and whether or not the status flips: events apply in the order
    the runner wrote them, so the last one is the run going on. A process that
    replaced one still reading running starts a run of its own."""
    state = await session.scalar(
        select(SessionState).where(*_key(key)).with_for_update()
    )
    if state is None:
        return
    state.run_epoch, state.run_seq = epoch, seq


async def running_runs(
    session: AsyncSession, seats: Collection[tuple[uuid.UUID, str]]
) -> dict[tuple[uuid.UUID, str], frozenset[tuple[str, int, int]]]:
    """{(conversation, agent): the runs its running sessions are in} for the
    given seats with a session the runner declares running, each run as
    (harness, epoch, seq). A seat with none running is left out."""
    if not seats:
        return {}
    wanted = set(seats)
    rows = await session.execute(
        select(
            SessionState.conversation_id,
            SessionState.agent_handle,
            SessionState.harness,
            SessionState.run_epoch,
            SessionState.run_seq,
        ).where(
            SessionState.worker_status == RUNNING,
            SessionState.conversation_id.in_({c for c, _ in wanted}),
        )
    )
    runs: dict[tuple[uuid.UUID, str], set[tuple[str, int, int]]] = {}
    for conversation, agent, harness, epoch, seq in rows:
        if (conversation, agent) not in wanted:
            continue
        found = runs.setdefault((conversation, agent), set())
        if epoch is not None and seq is not None:
            found.add((harness, epoch, seq))
    return {seat: frozenset(found) for seat, found in runs.items()}


def reports_lifecycle(harness: str) -> bool:
    """Whether ``harness``'s runner reports in the lifecycle protocol
    (`Harness.reports_lifecycle`): its sessions have a state here, it ends
    stuck work on its own clocks, and an input's read is its echo rather than
    the runner taking it."""
    return HARNESSES[harness].reports_lifecycle
