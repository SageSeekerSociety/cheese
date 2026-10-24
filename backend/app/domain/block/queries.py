"""block 领域的读入口：房间产出、项目批次事实与反应摘要。

## 为什么这里要有一层，而不是让路由自己去摸 repository

block 领域读得最勤的东西原本直通 `BlockRepository`，项目级的周报集就是其中之一。
HTTP 路由不属于任何领域，它每直接摸一次别人的 repository，就绕过一层这一层
本该守的约束（读什么、读完要不要补字段），而它恰恰是最容易随手摸过去的地方 ——
处理函数手上就有 session（见 `tests/unit/test_domain_import_guard.py` 的开头）。

所以这里不是搬运，是**出口**：项目级的读法有名字，交出去的是 `BlockOut`
（对外的形状）或反应摘要，不是 ORM 行。调用方只认这里的具名查询，
不再认识 `BlockRepository`，也就不会再有人从调用方
那边多摸一个方法出来。

这一层只做折叠，不做判断：顺序（最新在前）在 repository 里已经定了，这里原样
保留；`model_validate` 是唯一的加工。真正的判据（哪一块算周报）是
`BlockKind.weekly`，写在调用点，读的人一眼看得见。
"""

# Public reads leave transaction management to the caller: no explicit
# begin/commit/rollback; SQLAlchemy autobegin and autoflush are unchanged.

import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.asked import Awaited
from app.domain.block.indexed_rows import QUEUED_MESSAGE_ROWS, SHOWN_ROWS
from app.domain.block.models import READ_FAILED_META_KEY, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut, reply_preview

#: A message held back on purpose until its seat is free (`agent.pending_messages
#: .defer_message`): waiting however old it is.
DEFERRED_INPUT = "deferred_native_input"


def queued_messages(since, topic_id=None):
    """Messages that named an agent and have not had their turn: recent, or
    held back on purpose (`DEFERRED_INPUT`). Filtered by the literal predicate of
    `ix_blocks_queued_messages`, so a scan on a clock reads that index and not
    the whole table (`indexed_rows`). The pending-message scan starts turns
    from these (`agent.pending_messages`), and a task being written up does not
    close while one of its own is among them (`room_task.closing`)."""
    query = select(Block).where(
        QUEUED_MESSAGE_ROWS,
        or_(
            Block.created_at >= since,
            Block.meta[DEFERRED_INPUT].as_boolean().is_(True),
        ),
    )
    if topic_id is not None:
        query = query.where(Block.conversation_id == topic_id)
    return query.order_by(Block.created_at, Block.id)


def read_failed_messages(since, topic_id):
    """The messages of ``topic_id`` since ``since`` that were read into work
    that then failed (`READ_FAILED_META_KEY`): read, and not answered."""
    return (
        select(Block)
        .where(
            Block.conversation_id == topic_id,
            Block.created_at >= since,
            Block.meta[READ_FAILED_META_KEY].as_string().is_not(None),
        )
        .order_by(Block.created_at, Block.id)
    )


async def message_reply_target(
    session: AsyncSession, block_id: uuid.UUID
) -> Block | None:
    """Load the same-session reply target for conversation-edge validation."""
    return await BlockRepository(session).get(block_id)


async def reply_previews(
    session: AsyncSession, blocks: Iterable[Block]
) -> dict[uuid.UUID, dict]:
    """Previews for the replies among ``blocks``, keyed by the replying block.

    One query for the whole page, not one per reply. A parent from another
    conversation is not used: a reply is same-conversation by construction, and
    an older or corrupt edge must not carry a line across conversations.
    """
    items = list(blocks)
    parent_ids = {b.reply_to for b in items if b.reply_to is not None}
    if not parent_ids:
        return {}
    rows = await session.scalars(select(Block).where(Block.id.in_(parent_ids)))
    parents = {p.id: p for p in rows}
    previews: dict[uuid.UUID, dict] = {}
    for b in items:
        parent = parents.get(b.reply_to) if b.reply_to is not None else None
        if parent is not None and parent.conversation_id == b.conversation_id:
            previews[b.id] = reply_preview(parent)
    return previews


async def block_by_id(session: AsyncSession, block_id: uuid.UUID) -> Block | None:
    """The block a caller was handed by id, read in its own transaction."""
    return await BlockRepository(session).get(block_id)


async def inputs_after(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[Block]:
    """What a seat has not read: the newest participant messages after its
    cursor, oldest first."""
    return await BlockRepository(session).inputs_after(
        conversation_id, after=after, limit=limit
    )


async def notices_after(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    after: tuple[datetime, uuid.UUID] | None,
) -> list[Block]:
    """The platform notices a seat has not read, oldest first."""
    return await BlockRepository(session).notices_after(conversation_id, after=after)


async def turn_history(
    session: AsyncSession, conversation_id: uuid.UUID
) -> list[Block]:
    """Read the canonical turn-history ordering in the caller's transaction."""
    return await BlockRepository(session).turn_history(conversation_id)


async def turn_starts(
    session: AsyncSession, conversation_id: uuid.UUID
) -> list[tuple[datetime, uuid.UUID]]:
    """Read when each of this conversation's turns began, oldest first.

    The usage books need this: the metering proxy's log carries no work id, so
    a metered call is billed to whichever turn was under way at its timestamp
    (`usage.subscription_ingest`). Called through the read boundary because
    that reader sits a layer below this one.
    """
    return await BlockRepository(session).turn_starts(conversation_id)


async def earlier_message_count(
    session: AsyncSession, conversation_id: uuid.UUID, *, excluding: list[uuid.UUID]
) -> int:
    """Count the conversation's messages apart from the offered input batch."""
    return await BlockRepository(session).count_messages(
        conversation_id, excluding=excluding
    )


async def output_event_exists(
    session: AsyncSession, conversation_id: uuid.UUID, eid: str
) -> bool:
    """Read whether this conversation already materialized an output event."""
    return await BlockRepository(session).has_eid(conversation_id, eid)


async def turn_action_exists(
    session: AsyncSession, conversation_id: uuid.UUID, turn_id: uuid.UUID, resource: str
) -> bool:
    """Read the durable once-per-action predicate in the writer's transaction."""
    return await BlockRepository(session).has_action(conversation_id, turn_id, resource)


async def any_output_event_exists(
    session: AsyncSession, conversation_id: uuid.UUID, eids: list[str]
) -> bool:
    """Recognize every id carried by a coalesced output in the same transaction."""
    return await BlockRepository(session).has_any_eid(conversation_id, eids)


async def last_turn_output(
    session: AsyncSession, conversation_id: uuid.UUID, work_id: uuid.UUID
) -> str | None:
    """Read the turn's last said text, including progress, for closing dedupe."""
    return await BlockRepository(session).last_said_in_turn(conversation_id, work_id)


async def latest_preview(
    db: AsyncSession, conversation_id: uuid.UUID
) -> BlockOut | None:
    """Return the conversation's latest artifact through the block read
    boundary."""
    block = await BlockRepository(db).latest_artifact(conversation_id)
    return BlockOut.model_validate(block) if block is not None else None


async def latest_messages(
    db: AsyncSession, conversation_id: uuid.UUID, limit: int
) -> list[Block]:
    """The conversation's latest ``limit`` messages, oldest first."""
    page = await BlockRepository(db).page_for_topic(
        conversation_id, limit=limit, kinds=[BlockKind.message]
    )
    return list(page.items)


async def messages_page(
    db: AsyncSession, conversation_id: uuid.UUID, *, limit: int, skip: int = 0
) -> tuple[list[Block], int]:
    """A page of the conversation's messages read back from its newest, oldest
    of the page first, and how many messages it has in all. ``skip`` is how
    many of the newest the page leaves to the pages before it."""
    shown = (
        Block.conversation_id == conversation_id,
        Block.kind == BlockKind.message,
        SHOWN_ROWS,
    )
    rows = (
        await db.scalars(
            select(Block)
            .where(*shown)
            .order_by(Block.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
    ).all()
    total = await db.scalar(select(func.count()).select_from(Block).where(*shown))
    return list(reversed(rows)), int(total or 0)


async def newest_shown_seq(db: AsyncSession, conversation_id: uuid.UUID) -> int | None:
    """The number (`Block.seq`) of the last block stored in this conversation
    that the room shows (`shown`); None for one that shows nothing yet."""
    return await db.scalar(
        select(func.max(Block.seq)).where(
            Block.conversation_id == conversation_id, SHOWN_ROWS
        )
    )


async def weeklies_for_project(
    db: AsyncSession, project_id: uuid.UUID
) -> list[BlockOut]:
    """这个项目的周报集，最新在前；每条在 `meta` 里带它覆盖的那一段。

    一份周报说的是过去的一段时间，窗口（since/until）是那一行的身份。

    Callers authorize and own this session and its transaction. This read
    does not explicitly begin, commit or roll back.
    """
    blocks = await BlockRepository(db).list_by_kind_for_project(
        project_id, BlockKind.weekly
    )
    return [BlockOut.model_validate(block) for block in blocks]


async def awaiting_an_answer(
    db: AsyncSession, conversation_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, Awaited]:
    """这些对话里，哪几段停在一个未回答的提问上，各自在等谁 —— 一次查完。

    看板「待回答」那一格问的就是它：这是唯一一种会中断「运行中」的状态，所以它
    和别的批次事实一样，从外面喂进纯函数（见 `room_task/presentation.py`）。
    判据在 `BlockRepository.awaiting_an_answer` 里，这里只把路由和那条查询之间的
    名字固定下来。

    Callers authorize and own this session and its transaction. This read
    does not explicitly begin, commit or roll back.
    """
    return await BlockRepository(db).awaiting_an_answer(list(conversation_ids))


async def reaction_summaries_for_blocks(
    db: AsyncSession, block_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[dict]]:
    """Read ordered reaction counts and authors without exposing block storage.

    The caller owns authorization and the transaction. Native receipts use the
    same session so their effects and these summaries commit together.
    """
    return await BlockRepository(db).reactions_for_blocks(block_ids)
