"""Keep a run record and tell whoever has its conversation open.

`run_record.service.record` writes the row; this half commits it and pushes
the `run_record` frame through the room broker, which lives with the agent
runtime.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.domain.agent.realtime.broker import get_broker
from app.domain.run_record.models import RunRecord
from app.domain.run_record.service import (
    FRAME,
    THREAD_FRAME,
    as_payload,
    of_conversation,
    record,
    restate,
)
from app.domain.thread.models import Thread

logger = logging.getLogger(__name__)


async def publish(
    kept: RunRecord | dict | None,
    channel: str | None = None,
    room: uuid.UUID | None = None,
) -> None:
    """Tell whoever has the conversation open — on `channel` when the caller
    names the socket its turn talks on, else on the conversation's own. When
    the conversation is a 支线, its channel's main line hears it too
    (`THREAD_FRAME`): the line under the message says what the teammate
    answering there is waiting for. Never raises: a status line is not worth
    the work it describes."""
    if kept is None:
        return
    payload = kept if isinstance(kept, dict) else as_payload(kept)
    channel = channel or payload.get("conversation_id")
    if not channel:
        return

    try:
        await get_broker().publish(channel, {"type": FRAME, "record": payload})
        if room is not None:
            await get_broker().publish(
                str(room),
                {
                    "type": THREAD_FRAME,
                    "thread_id": payload.get("conversation_id"),
                    "record": payload,
                },
            )
    except Exception:  # see above
        logger.exception("could not publish run record %s", payload.get("id"))


async def _thread_room(session: AsyncSession, conversation_id) -> uuid.UUID | None:
    """The channel whose 支线 this conversation is; None when it is not one."""
    return await session.scalar(
        select(Thread.room_id).where(Thread.id == conversation_id)
    )


async def record_now(
    sessions: SessionFactory,
    *,
    conversation_id: uuid.UUID,
    content: str,
    meta: dict,
    turn_id: uuid.UUID | None = None,
    seat: str | None = None,
    channel: str | None = None,
) -> dict | None:
    """`record` in a session of its own, committed and published. For callers
    outside a request's transaction (the turn runner, the hook stream). Returns
    the published payload, or None when nothing was written."""
    try:
        async with sessions() as session:
            kept = await record(
                session,
                conversation_id=conversation_id,
                content=content,
                meta=meta,
                turn_id=turn_id,
                seat=seat,
            )
            if kept is None:
                return None
            payload = as_payload(kept)
            room = await _thread_room(session, conversation_id)
            await session.commit()
    except Exception:  # see `publish`
        logger.exception("could not keep run record %s", content)
        return None
    await publish(payload, channel, room)
    return payload


async def restate_now(
    sessions: SessionFactory,
    record_id: uuid.UUID,
    *,
    content: str,
    meta: dict,
    channel: str | None = None,
) -> dict | None:
    """Append the next status in a record's series, committed and published."""
    try:
        async with sessions() as session:
            kept = await restate(session, record_id, content=content, meta=meta)
            if kept is None:
                return None
            payload = as_payload(kept)
            room = await _thread_room(session, kept.conversation_id)
            await session.commit()
    except Exception:  # see `publish`
        logger.exception("could not restate run record %s", record_id)
        return None
    await publish(payload, channel, room)
    return payload


async def for_site(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    since: datetime | None,
    until: datetime | None,
    author: str | None = None,
    limit: int = 500,
    before: tuple[datetime, str, str, int] | None = None,
    after: tuple[datetime, str, str, int] | None = None,
) -> list[dict]:
    """The conversation's run records between two moments, as the 现场 reads
    them (oldest first). `author` keeps only that teammate's.

    `before` walks up from a page in hand and `after` walks forward from one;
    each is a 现场 cursor — a moment, the family that wrote it, its seat and
    its id — turned into this table's own (moment, seat, id) bound."""
    upper = None if before is None else _bound(before, forward=False)
    lower = None if after is None else _bound(after, forward=True)
    kept = await of_conversation(
        session,
        conversation_id,
        since=since,
        until=until,
        limit=limit,
        seat=author,
        before=upper,
        after=lower,
    )
    return [as_payload(r) for r in kept if (author is None or r.seat == author)]


def _bound(
    key: tuple[datetime, str, str, int], *, forward: bool
) -> tuple[datetime, str, uuid.UUID]:
    """A 现场 cursor as the run-record table's (moment, seat, id) bound.

    A cursor's middle field says which family wrote it. `platform` rows carry
    their seat verbatim. Anything else is a record or a pre-launch turn, whose
    seat here is empty — walking up that ends at "nothing at this moment", and
    walking forward it starts at "everything at this moment" (a row already on
    screen is dropped by id when the client merges, so taking the whole moment
    is the safe side of the bound)."""
    at, source, seat, sequence = key
    if source == "platform":
        return (at, seat, uuid.UUID(int=sequence))
    if forward:
        return (at, "", uuid.UUID(int=0))
    if source < "platform":
        return (at, "", uuid.UUID(int=0))
    return (at, "\U0010ffff", uuid.UUID(int=2**128 - 1))
