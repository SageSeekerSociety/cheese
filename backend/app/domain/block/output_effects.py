"""Same-session timeline output effects, without transaction or publication ownership.

The caller owns idempotency, attribution and the encompassing transaction. An
append returns the flushed row in that session: mention effects still update its
refs before the caller serializes and commits it.
"""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository


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
) -> Block:
    """Append an attributable timeline row with the canonical pending-input rules.

    Flush and refresh are part of the block write; committing is not. Returning
    this same-session row lets the caller attach question/mention effects in the
    very transaction that makes the output durable.
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
    )


async def fail_step(
    session: AsyncSession, block_id: uuid.UUID, error: str
) -> Block | None:
    """Mark the timeline step failed in the caller's current transaction."""
    return await BlockRepository(session).mark_step_failed(block_id, error)


async def retain_step_output(
    session: AsyncSession, block_id: uuid.UUID, tail: str, total: int
) -> Block | None:
    """Attach the already bounded output tail and its original length."""
    return await BlockRepository(session).record_step_output(block_id, tail, total)
