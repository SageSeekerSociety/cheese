"""Event output transactions: dedupe, row write, payload, then commit.

No publication or live-owner lookup occurs here. The one fallback attribution
capability performs SQL on this transaction's session, only after eid dedupe.
"""

import logging
import uuid
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.agent.step_output import output_tail
from app.domain.block.about import EventAbout, landing
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut

logger = logging.getLogger("app.domain.agent.room_events")


class EventAuthorResolver(Protocol):
    async def __call__(self, session: AsyncSession, topic_id: uuid.UUID) -> str: ...


async def persist_room_event(
    sessions: async_sessionmaker[AsyncSession],
    *,
    resolve_author: EventAuthorResolver,
    acting_agent: str | None,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    content: str,
    meta: dict,
    turn_id: uuid.UUID | None,
    eid: str | None = None,
    platform_unsolicited: bool = False,
    in_room: bool = False,
    author_type: AuthorType = AuthorType.participant,
    inner_id: uuid.UUID | None = None,
    author: str | None = None,
    at: datetime | None = None,
) -> dict | None:
    """One event block, committed NOW and deduped by event-id.

    Shared by everything the room learns mid-turn — a tool call, a subagent's
    conclusion, the turn's change summary — so all three get the same
    durability and idempotency contract instead of three copies of it that
    drift. Returns None when this event-id already landed.

    ``in_room`` decides whether the conversation shows it at all, and it
    travels as ``meta.in_room`` — its own field, because visibility is not
    authorship. It used to ride on ``author_type``, which meant an event
    genuinely written by 芝士 could not be shown in the room without lying
    about who wrote it, and anything that later wanted to know the author
    was reading a field answering a different question. Absent means shown:
    every other writer in the codebase posts to the room.

    ``author_type`` is then free to answer its own question, and does: 芝士
    is a participant and wrote the tool calls and the subagent conclusions,
    while the change summary is the platform's own line."""
    meta = {**meta, "in_room": in_room}
    if eid:
        meta = {**meta, "eid": eid}
    if platform_unsolicited:
        meta = {**meta, "platform_unsolicited": True}
    async with sessions() as session:
        blocks = BlockRepository(session)
        if eid and await blocks.has_eid(inner_id or topic_id, eid):
            return None
        # 「关于什么」由 `inner_id` 推出，调用方不另声明：调用方说出这条事件
        # 关于什么的方式**就是**递不递一张卡下来（变更提醒从不递）。再收一个
        # about 形参，是同一个事实在一处声明两遍——不加 `about_kind` 列的同一条理由。
        landed = landing(
            EventAbout.task if inner_id is not None else EventAbout.room,
            project_id=project_id,
            room_id=topic_id,
            task_id=inner_id,
        )
        block = await blocks.add(
            project_id=landed.project_id,
            conversation_id=landed.conversation_id,
            author=(author or acting_agent or await resolve_author(session, topic_id)),
            author_type=author_type,
            content=content,
            kind=BlockKind.event,
            turn_id=turn_id,
            meta=meta,
            created_at=at,
        )
        payload = BlockOut.model_validate(block).model_dump(mode="json")
        await session.commit()
    return payload


async def _mark_step_failed(
    sessions: async_sessionmaker, block_id: uuid.UUID, error: str
) -> dict | None:
    """Stamp a 现场 step as failed, and hand back the step as it now reads.
    Never fails a turn over a red dot."""
    try:
        async with sessions() as session:
            block = await BlockRepository(session).mark_step_failed(block_id, error)
            payload = (
                BlockOut.model_validate(block).model_dump(mode="json")
                if block is not None
                else None
            )
            await session.commit()
        return payload
    except Exception:  # noqa: BLE001 — a step's verdict is not worth a turn
        logger.warning("could not mark step %s failed", block_id)
        return None


async def _record_step_output(
    sessions: async_sessionmaker, block_id: uuid.UUID, text: str
) -> dict | None:
    """Keep the tail of what a step printed. Never fails a turn over it."""
    output, total = output_tail(text)
    try:
        async with sessions() as session:
            block = await BlockRepository(session).record_step_output(
                block_id, output, total
            )
            payload = (
                BlockOut.model_validate(block).model_dump(mode="json")
                if block is not None
                else None
            )
            await session.commit()
        return payload
    except Exception:  # noqa: BLE001 — a step's output is not worth a turn
        logger.warning("could not record the output of step %s", block_id)
        return None
