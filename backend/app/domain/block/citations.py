"""Which conversations' messages reference something by ``<#id>`` — a
channel, a task, a 支线 or a document (#3827). A message's ``<#id>``
references are in its ``refs`` as ``topic:<id>``
(`agent.mentions.announce_mentions`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import Block, BlockKind


@dataclass(frozen=True)
class Citing:
    """A conversation whose messages reference a document: how many do, and
    the latest of them."""

    conversation_id: uuid.UUID
    count: int
    block_id: uuid.UUID
    content: str
    author: str
    at: datetime


#: How many of the latest referencing messages are read to group them.
_CITING_READ = 500


async def citing_conversations(
    db: AsyncSession, project_ids: list[uuid.UUID], document_id: uuid.UUID
) -> list[Citing]:
    """The conversations of these projects whose messages reference
    ``document_id``, the most recently referencing first."""
    if not project_ids:
        return []
    rows = await db.execute(
        select(
            Block.id,
            Block.conversation_id,
            Block.content,
            Block.author,
            Block.created_at,
        )
        .where(
            Block.project_id.in_(project_ids),
            Block.kind == BlockKind.message,
            cast(Block.refs, JSONB).contains([f"topic:{document_id}"]),
        )
        .order_by(Block.created_at.desc())
        .limit(_CITING_READ)
    )
    grouped: dict[uuid.UUID, Citing] = {}
    for block_id, conversation_id, content, author, at in rows:
        seen = grouped.get(conversation_id)
        if seen is None:
            grouped[conversation_id] = Citing(
                conversation_id, 1, block_id, content or "", author, at
            )
        else:
            grouped[conversation_id] = replace(seen, count=seen.count + 1)
    return list(grouped.values())
