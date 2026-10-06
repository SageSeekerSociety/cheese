"""Keep a run record and tell whoever has its conversation open.

`run_record.service.record` writes the row; this half commits it and pushes
the `run_record` frame through the room broker, which lives with the agent
runtime.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.run_record.models import RunRecord
from app.domain.run_record.service import (
    FRAME,
    as_payload,
    of_conversation,
    record,
    restate,
)

logger = logging.getLogger(__name__)


async def publish(kept: RunRecord | dict | None, channel: str | None = None) -> None:
    """Tell whoever has the conversation open — on `channel` when the caller
    names the socket its turn talks on, else on the conversation's own. Never
    raises: a status line is not worth the work it describes."""
    if kept is None:
        return
    payload = kept if isinstance(kept, dict) else as_payload(kept)
    channel = channel or payload.get("conversation_id")
    if not channel:
        return
    from app.domain.agent.runtime import get_broker

    try:
        await get_broker().publish(channel, {"type": FRAME, "record": payload})
    except Exception:  # noqa: BLE001 — see above
        logger.exception("could not publish run record %s", payload.get("id"))


async def record_now(
    sessions: async_sessionmaker,
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
            await session.commit()
    except Exception:  # noqa: BLE001 — see `publish`
        logger.exception("could not keep run record %s", content)
        return None
    await publish(payload, channel)
    return payload


async def restate_now(
    sessions: async_sessionmaker,
    record_id: uuid.UUID,
    *,
    content: str,
    meta: dict,
    channel: str | None = None,
) -> dict | None:
    """Restate a record in place, committed and published."""
    try:
        async with sessions() as session:
            kept = await restate(session, record_id, content=content, meta=meta)
            if kept is None:
                return None
            payload = as_payload(kept)
            await session.commit()
    except Exception:  # noqa: BLE001 — see `publish`
        logger.exception("could not restate run record %s", record_id)
        return None
    await publish(payload, channel)
    return payload


async def for_site(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    since: datetime | None,
    until: datetime | None,
    author: str | None = None,
) -> list[dict]:
    """The conversation's run records between two moments, as the 现场 reads
    them (oldest first). `author` keeps only that teammate's."""
    kept = await of_conversation(session, conversation_id, since=since, until=until)
    return [as_payload(r) for r in kept if author is None or r.seat == author]
