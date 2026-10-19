"""A seat's session as its runner declares it, and the events it applied once.

The runner reports in the lifecycle protocol (`harness/driven/lifecycle.py`).
Here is what the platform keeps of it durably: the worker epoch it issued the
seat's runner (``register``), the worker status the runner last declared, and
how far each registration's events have been applied (``take``). Everything
that changes because of an event is written in the transaction ``take`` runs
in, so an event is applied exactly once whatever is redelivered.
"""

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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


async def take(
    session: AsyncSession, key: SeatKey, *, worker_epoch: int, epoch: int, seq: int
) -> bool:
    """Claim one event for this transaction: True when it is new and its
    effects are to be applied now, False when it was applied before.

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
    return claimed is not None


async def declare(
    session: AsyncSession,
    key: SeatKey,
    status: str,
    at: datetime,
    *,
    exited: bool = False,
) -> None:
    """What the runner declared the session to be doing, as of ``at``."""
    state = await session.scalar(
        select(SessionState).where(*_key(key)).with_for_update()
    )
    if state is None:
        return
    if state.worker_status != status:
        state.worker_status = status
        state.changed_at = at
    if exited:
        state.exited_at = at


async def running_conversations(
    session: AsyncSession, conversation_ids: Collection[uuid.UUID] | None = None
) -> set[uuid.UUID]:
    """The conversations at least one seat of which has a session its runner
    declared ``running``; of ``conversation_ids`` only, when given."""
    query = select(SessionState.conversation_id).where(
        SessionState.worker_status == RUNNING
    )
    if conversation_ids is not None:
        if not conversation_ids:
            return set()
        query = query.where(SessionState.conversation_id.in_(set(conversation_ids)))
    return set(await session.scalars(query.distinct()))
