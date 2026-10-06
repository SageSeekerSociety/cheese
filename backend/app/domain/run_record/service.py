"""平台记下一条运行记录 —— 不进对话，进现场和管理后台。

和 `announce` 是一对：`announce` 是平台在对话里说一句话，人要读到；这里是平台把
运行中的一件事记下来，想查的人去现场看。哪些事走哪一边，看
`platform_notices.RUN_RECORD_EVENTS`。

记下之后推一帧 `run_record` 到这段对话的通道上（`agent.run_records`）：现场接
着显示它，输入框上方那行「芝士 排队中」也从这一帧读芝士此刻在等什么。帧里那份
记录长得和一条事件块一样（`as_payload`），现场照旧按块的样子排它，只是聊天区不
收。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.domain.conversation.models import Conversation
from app.domain.run_record.models import RETENTION, RunRecord
from app.domain.run_record.repository import RunRecordRepository

#: 帧的类型。新建和改写都用它：前端按 id 换掉旧的那一份。
FRAME = "run_record"


def as_payload(record: RunRecord) -> dict:
    """The record as the 现场 reads a platform event: the shape of a block."""
    meta = dict(record.meta or {})
    if record.seat:
        meta.setdefault("seat", record.seat)
    return {
        "id": str(record.id),
        "conversation_id": (
            str(record.conversation_id) if record.conversation_id else None
        ),
        "kind": "event",
        "author_type": "platform",
        "author": record.seat or "system",
        "content": record.content,
        "reply_to": None,
        "refs": [],
        "turn_id": str(record.turn_id) if record.turn_id else None,
        "meta": meta,
        # Not a block of the conversation: what pushes it picks the frame by this.
        "run_record": True,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


async def record(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    content: str,
    meta: dict,
    turn_id: uuid.UUID | None = None,
    seat: str | None = None,
    record_id: uuid.UUID | None = None,
) -> RunRecord | None:
    """Keep a record on the conversation `conversation_id` (a room's, a task's
    or a 支线's). Writes the caller's session; does not commit or publish —
    `publish` after the commit. None when the conversation is gone."""
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        return None
    return await RunRecordRepository(session).add(
        project_id=conversation.project_id,
        conversation_id=conversation_id,
        content=content,
        meta=meta,
        turn_id=turn_id,
        seat=seat if seat and seat != "system" else None,
        record_id=record_id,
    )


async def purge_expired(sessions: SessionFactory) -> int:
    """Delete the records older than `RETENTION`. Returns how many."""
    async with sessions() as session:
        gone = await RunRecordRepository(session).delete_before(
            datetime.now(UTC) - RETENTION
        )
        await session.commit()
    return gone


async def keep(
    session: AsyncSession,
    *,
    project_id: uuid.UUID | None,
    content: str,
    meta: dict,
    conversation_id: uuid.UUID | None = None,
    record_id: uuid.UUID | None = None,
) -> RunRecord:
    """Keep a record whose project the caller already knows — one that may
    belong to no conversation (the platform's own errors)."""
    return await RunRecordRepository(session).add(
        project_id=project_id,
        conversation_id=conversation_id,
        content=content,
        meta=meta,
        record_id=record_id,
    )


async def running(
    session: AsyncSession, turn_id: uuid.UUID, kind: str
) -> list[uuid.UUID]:
    """The turn's records of `kind` still saying their news is under way."""
    return await RunRecordRepository(session).running(turn_id, kind)


async def restate(
    session: AsyncSession, record_id: uuid.UUID, *, content: str, meta: dict
) -> RunRecord | None:
    return await RunRecordRepository(session).restate(
        record_id, content=content, meta=meta
    )


async def of_conversation(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    since: datetime | None,
    until: datetime | None,
) -> list[RunRecord]:
    return await RunRecordRepository(session).of_conversation(
        conversation_id, since=since, until=until
    )
