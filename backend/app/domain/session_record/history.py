"""Pre-launch turns have an explanation, never a reconstructed legacy timeline."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Uuid, column, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.conversation.models import Conversation
from app.domain.session_record.models import SessionRecord
from app.domain.session_record.service import Key, encode_cursor

_turns = table(
    "agent_turns",
    column("id", Uuid),
    column("conversation_id", Uuid),
    column("agent_handle", String),
    column("started_at", DateTime(timezone=True)),
)


async def earlier_turns(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    before: Key | None,
    author: str | None,
    limit: int,
) -> tuple[list[dict], bool, datetime]:
    boundary = await db.scalar(
        select(Conversation.raw_records_started_at).where(
            Conversation.id == conversation_id
        )
    )
    assert boundary is not None
    query = select(_turns).where(
        _turns.c.conversation_id == conversation_id,
        _turns.c.started_at < boundary,
        ~select(SessionRecord.id).where(SessionRecord.turn_id == _turns.c.id).exists(),
    )
    if before is not None:
        query = query.where(_turns.c.started_at < before[0])
    if author is not None:
        query = query.where(_turns.c.agent_handle == author)
    turns = list(
        (
            await db.execute(
                query.order_by(_turns.c.started_at.desc()).limit(limit + 1)
            )
        ).all()
    )
    return (
        [
            {
                "id": str(turn.id),
                "conversation_id": str(conversation_id),
                "turn_id": str(turn.id),
                "kind": "event",
                "author_type": "platform",
                "author": turn.agent_handle or "system",
                "content": "",
                "created_at": turn.started_at.isoformat(),
                "updated_at": None,
                "refs": [],
                "reply_to": None,
                "meta": {
                    "before_launch": True,
                    "cursor": encode_cursor((turn.started_at, "", str(turn.id), 0)),
                },
            }
            for turn in reversed(turns[:limit])
        ],
        len(turns) > limit,
        boundary,
    )
