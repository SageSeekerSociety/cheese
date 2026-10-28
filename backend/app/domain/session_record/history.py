"""Pre-launch turns have an explanation, never a reconstructed legacy timeline."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Uuid, and_, column, or_, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.conversation.models import Conversation
from app.domain.session_record.models import SessionRecord
from app.domain.session_record.service import Key, encode_cursor

# `agent` sits layers above this package, so its table is read bare; the
# copy is held against the model by `test_a_turn_read_mirrors_the_tables_it_may_not_import`.
_turns = table(
    "agent_turns",
    column("id", Uuid),
    column("conversation_id", Uuid),
    column("agent_handle", String),
    column("started_at", DateTime(timezone=True)),
    column("stopped_at", DateTime(timezone=True)),
)


def _after_a_turn(turns, key: Key, *, forward: bool):
    """The turns on the far side of `key`, ties broken the way the cursor orders them.

    A pre-launch turn's cursor is `(started_at, "", turn id, 0)`, so two turns that
    started in the same instant are still strictly ordered — by id. Comparing the
    timestamps alone would drop the second one: paging forward past the first
    would never reach it, and paging up past it would return it twice."""
    at, source, seat, _sequence = key
    if source != "":
        # Not a turn's own cursor (a record's, or a platform line's): every turn at
        # that instant sorts before it, so the comparison falls back to the moment.
        return turns.c.started_at < at if not forward else turns.c.started_at > at
    tied = turns.c.started_at == at
    if forward:
        return or_(turns.c.started_at > at, and_(tied, turns.c.id.cast(String) > seat))
    return or_(turns.c.started_at < at, and_(tied, turns.c.id.cast(String) < seat))


async def earlier_turns(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    before: Key | None,
    author: str | None,
    limit: int,
    after: Key | None = None,
) -> tuple[list[dict], bool, datetime]:
    """The turns that ran before raw records began, newest first — or, with
    `after`, the oldest ones still newer than that cursor.

    `before` and `after` are the two directions a reader pages in: `before`
    walks up into the past, `after` walks forward from a window already held.
    Only one of them is ever given. The flag it returns says whether another
    page exists in that same direction."""
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
        query = query.where(_after_a_turn(_turns, before, forward=False))
    if after is not None:
        query = query.where(_after_a_turn(_turns, after, forward=True))
    if author is not None:
        query = query.where(_turns.c.agent_handle == author)
    # 往回翻从最新的一头取，往前翻从最旧的一头取：两次都是「顺着读者去的方向」。
    order = (
        _turns.c.started_at.asc() if after is not None else _turns.c.started_at.desc()
    )
    turns = list((await db.execute(query.order_by(order).limit(limit + 1))).all())
    kept = turns[:limit]
    if after is None:
        kept = list(reversed(kept))
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
            for turn in kept
        ],
        len(turns) > limit,
        boundary,
    )
