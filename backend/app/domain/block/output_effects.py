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

#: `Session.info` key: the conversations whose numbering this transaction holds,
#: each with the savepoint it was taken in (None: outside any). PostgreSQL lets
#: one go when that savepoint is rolled back, and keeps it when it is released.
NUMBERING_HELD = "numbering_held"

_TAKE_NUMBERING = text(
    "SELECT pg_advisory_xact_lock(:key, hashtext(CAST(:conversation AS text)))"
)


class NumberingOutOfOrder(RuntimeError):
    """A transaction already holding numbering locks asked for another one.

    Numbering locks are taken in one order, conversation id order, all at
    once: a transaction that will write into several conversations' rows
    (several tasks) names them all in one call, before any of them is held.
    One that holds some and then takes another can take it after a lock a
    second transaction already holds while that one waits for a lock the first
    holds."""


def hold_numbering_now(session: Session, conversations: Iterable[uuid.UUID]) -> None:
    """Take these conversations' numbering locks on ``session``'s connection,
    in conversation id order, all of them now; held until the transaction
    ends. Synchronous, for code already inside a flush or a statement's
    execution (`room_task.lock_order`).

    Every lock a transaction takes through here comes from one call: asking
    again for ones it holds takes nothing, and asking for another while it
    holds some is refused (`NumberingOutOfOrder`)."""
    held: dict[uuid.UUID, object] = session.info.setdefault(NUMBERING_HELD, {})
    wanted = set(conversations) - held.keys()
    if not wanted:
        return
    if held:
        # i18n-exempt: programming error, raised before the lock is taken
        raise NumberingOutOfOrder(
            "this transaction holds the numbering of "
            f"{sorted(map(str, held))} and asked for {sorted(map(str, wanted))}: "
            "name every conversation it will lock in one call, first "
            "(room_task.lock_order.lock_tasks_for_change)"
        )
    savepoint = session.get_nested_transaction()
    for conversation in sorted(wanted, key=str):
        session.connection().execute(
            _TAKE_NUMBERING,
            {"key": NUMBERING_LOCK, "conversation": str(conversation)},
        )
        held[conversation] = savepoint


async def hold_numbering(session: AsyncSession, conversation_id: uuid.UUID) -> None:
    """Take ``conversation_id``'s numbering lock now, ahead of the first block
    this transaction writes there; held until it ends. The trigger takes the
    same lock again on each block, which a transaction already holding it
    passes straight through."""
    await session.run_sync(hold_numbering_now, [conversation_id])


@event.listens_for(Session, "after_rollback")
def _numbering_rolled_back(session: Session) -> None:
    # Fired while the transaction rolling back is still the innermost one.
    held = session.info.get(NUMBERING_HELD)
    if not held:
        return
    savepoint = session.get_nested_transaction()
    if savepoint is None:
        held.clear()
        return
    for conversation in [c for c, taken in held.items() if taken is savepoint]:
        del held[conversation]


@event.listens_for(Session, "after_transaction_end")
def _numbering_released(session: Session, transaction) -> None:
    held = session.info.get(NUMBERING_HELD)
    if held is None:
        return
    if transaction.parent is None:
        session.info.pop(NUMBERING_HELD, None)
        return
    if not transaction.nested:
        return
    # A savepoint released: what was taken in it is held by the one around it.
    outer = transaction.parent
    while outer is not None and not outer.nested:
        outer = outer.parent
    for conversation, taken in held.items():
        if taken is transaction:
            held[conversation] = outer
