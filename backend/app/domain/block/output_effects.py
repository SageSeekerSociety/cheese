"""Same-session timeline output effects, without transaction or publication ownership.

The caller owns idempotency, attribution and the encompassing transaction. An
append returns the flushed row in that session: mention effects still update its
refs before the caller serializes and commits it.
"""

import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.domain.block.models import Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.event_author import AuthorType


async def append_output(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    author: str,
    author_type: AuthorType,
    content: str,
    kind: BlockKind,
    reply_to: uuid.UUID | None = None,
    turn_id: uuid.UUID | None = None,
    mime_type: str | None = None,
    meta: dict | None = None,
    created_at: datetime | None = None,
    own_output: bool = False,
    block_id: uuid.UUID | None = None,
) -> Block:
    """Append an attributable timeline row with the canonical pending-input rules.

    The flush is part of the block write; committing is not. Returning this
    same-session row lets the caller attach question/mention effects in the very
    transaction that makes the output durable.
    """
    return await BlockRepository(session).add(
        project_id=project_id,
        conversation_id=conversation_id,
        author=author,
        author_type=author_type,
        content=content,
        kind=kind,
        reply_to=reply_to,
        turn_id=turn_id,
        mime_type=mime_type,
        meta=meta,
        created_at=created_at,
        own_output=own_output,
        block_id=block_id,
    )


async def count_prompt_attempt(
    session: AsyncSession, block_ids: list[uuid.UUID], turn_id: uuid.UUID
) -> int:
    """Count this exact offered batch; the caller commits its assembly checkpoint."""
    return await BlockRepository(session).bump_prompt_attempts(block_ids, turn_id)


async def withdraw_prompt_claims(
    session: AsyncSession, block_ids: list[uuid.UUID]
) -> None:
    """Drop a failed prompt's claims without committing or consuming its inputs."""
    await BlockRepository(session).forget_prompted_turn(block_ids)


async def consume_prompt(
    session: AsyncSession, block_ids: list[uuid.UUID], turn_id: uuid.UUID
) -> None:
    """Consume a non-native prompt in the encompassing stop transaction."""
    await BlockRepository(session).mark_consumed(block_ids, turn_id)


#: The first key of every conversation's numbering lock: the advisory lock the
#: `block_numbered()` trigger takes on each new block (alembic `0dd66b328211`).
NUMBERING_LOCK = 1_734_632_801

#: `Session.info` key: the conversations whose numbering this transaction holds.
NUMBERING_HELD = "numbering_held"

_TAKE_NUMBERING = text(
    "SELECT pg_advisory_xact_lock(:key, hashtext(CAST(:conversation AS text)))"
)


def hold_numbering_now(session: Session, conversations: Iterable[uuid.UUID]) -> None:
    """Take these conversations' numbering locks on ``session``'s connection,
    in id order, the ones this transaction does not hold yet; held until it
    ends. Synchronous, for code already inside a flush or a statement's
    execution (`room_task.lock_order`)."""
    held: set[uuid.UUID] = session.info.setdefault(NUMBERING_HELD, set())
    for conversation in sorted(set(conversations) - held, key=str):
        session.connection().execute(
            _TAKE_NUMBERING,
            {"key": NUMBERING_LOCK, "conversation": str(conversation)},
        )
        held.add(conversation)


async def hold_numbering(session: AsyncSession, conversation_id: uuid.UUID) -> None:
    """Take ``conversation_id``'s numbering lock now, ahead of the first block
    this transaction writes there; held until it ends. The trigger takes the
    same lock again on each block, which a transaction already holding it
    passes straight through."""
    await session.run_sync(hold_numbering_now, [conversation_id])


@event.listens_for(Session, "after_transaction_end")
def _numbering_released(session: Session, _transaction) -> None:
    # A savepoint rolled back releases the locks taken inside it; forgetting on
    # every end, savepoints included, only costs taking one again.
    session.info.pop(NUMBERING_HELD, None)
