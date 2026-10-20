"""Where each session in a conversation stands, read off its raw records.

A session that is part-way through a tool call has written the call and not
yet its result. That is the shape a turn leaves when its process dies between
starting something and hearing back, and `TopicService.stall_signal` asks for
it here: the journal is the one place that records every call and every
result, whichever harness wrote them.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import StorageBackend
from app.domain.session_record.history import _turns
from app.domain.session_record.models import SessionRecord
from app.domain.session_record.service import open_run
from app.domain.session_record.views import parts


@dataclass(frozen=True)
class OpenCall:
    """A tool call the session wrote and never wrote a result for."""

    tool: str
    call_id: str
    at: datetime


@dataclass(frozen=True)
class SessionTail:
    """One session's newest moment in a conversation."""

    agent_handle: str
    harness: str
    # When the session last wrote anything.
    last_at: datetime
    # The call its current turn is still waiting on, if any. A turn the
    # platform has already closed (it ended, failed, timed out or was swept)
    # waits on nothing, whatever its records stopped at.
    open_call: OpenCall | None


async def tails(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    storage: StorageBackend | None = None,
) -> list[SessionTail]:
    """Every session that wrote in this conversation, newest first."""
    newest = (
        await db.scalars(
            select(SessionRecord)
            .where(SessionRecord.conversation_id == conversation_id)
            .distinct(SessionRecord.agent_handle, SessionRecord.harness)
            .order_by(
                SessionRecord.agent_handle,
                SessionRecord.harness,
                SessionRecord.last_at.desc(),
                SessionRecord.last_sequence.desc(),
            )
        )
    ).all()
    found = [
        SessionTail(
            agent_handle=row.agent_handle,
            harness=row.harness,
            last_at=row.last_at,
            open_call=await _open_call(db, row, storage),
        )
        for row in newest
    ]
    return sorted(found, key=lambda tail: tail.last_at, reverse=True)


async def _open_call(
    db: AsyncSession, newest: SessionRecord, storage: StorageBackend | None
) -> OpenCall | None:
    """The call the session's current turn is waiting on.

    Only a turn the platform still holds open can be waiting. Its records are
    read newest first, so a call's result, which the journal always writes
    after the call, is in hand before the call is. The walk ends at the first
    call with no result, at the record that ended the turn, or at the start of
    the turn: a turn's records never answer another turn's call.
    """
    if newest.turn_id is None or not await db.scalar(
        select(_turns.c.id).where(
            _turns.c.id == newest.turn_id, _turns.c.stopped_at.is_(None)
        )
    ):
        return None
    rows = await db.stream_scalars(
        select(SessionRecord)
        .where(
            SessionRecord.conversation_id == newest.conversation_id,
            SessionRecord.agent_handle == newest.agent_handle,
            SessionRecord.harness == newest.harness,
            SessionRecord.turn_id == newest.turn_id,
        )
        .order_by(SessionRecord.last_at.desc(), SessionRecord.last_sequence.desc())
        .execution_options(yield_per=20)
    )
    answered: set[str] = set()
    async for row in rows:
        entries = await open_run(row, storage)
        for entry in reversed(entries):
            record = entry["record"]
            if record.get("type") == "result":
                return None
            for part in reversed(parts(record)):
                if part["kind"] == "output":
                    answered.add(part["call_id"])
                elif part["kind"] == "tool" and part["call_id"] not in answered:
                    return OpenCall(
                        tool=part["name"],
                        call_id=part["call_id"],
                        at=datetime.fromisoformat(entry["at"]),
                    )
    return None
