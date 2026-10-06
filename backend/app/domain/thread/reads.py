"""What a page shows of 支线: the line under each message in the main line,
and the channel's list of 支线.

Unread counts only for the people who took part — who wrote the message the
支线 hangs under or said something in it — and only what people said: the
same rule as a task's (`TopicRepository.unread_counts`). 芝士 answers in
every 支线 it is asked in, and a mark that lights at each answer is one nobody
reads.
"""

import uuid

from sqlalchemy import (
    DateTime,
    String,
    Uuid,
    and_,
    any_,
    bindparam,
    column,
    func,
    or_,
    select,
    table,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.identity.handles import agent_handle_column, names_a_person
from app.domain.thread.models import Thread

# Bare tables: ``topic`` and ``room_task`` resolve 支线 (`room_task.place`), and
# importing them back would make a cycle.
_reads = table(
    "topic_read_states",
    column("topic_id", Uuid),
    column("user_handle", String),
    column("last_read_at", DateTime(timezone=True)),
)
_tasks = table(
    "tasks",
    column("id", Uuid),
    column("title", String),
    column("status", String),
    column("upgraded_from_block_id", Uuid),
)


def _among(ids):
    """One array parameter, not an IN list: a busy channel's whole timeline is
    tens of thousands of ids, and asyncpg refuses more than 32767 bound values
    in one statement."""
    return any_(bindparam(None, list(ids), type_=ARRAY(Uuid)))


#: How much of the last reply the main line shows: two lines of it.
LAST_REPLY_CHARS = 200


async def said_in(session: AsyncSession, block_id: uuid.UUID) -> uuid.UUID | None:
    """The conversation a message was said in, or None when there is no such
    message."""
    return await session.scalar(
        select(Block.conversation_id).where(Block.id == block_id)
    )


async def root_of(session: AsyncSession, thread: Thread) -> Block | None:
    """The message a 支线 hangs under."""
    return await session.get(Block, thread.root_block_id)


async def said_before(session: AsyncSession, root: Block, *, limit: int) -> list[Block]:
    """The ``limit`` messages said in the main line just before the one a 支线
    hangs under, oldest first: what was being talked about when it was said."""
    rows = await session.scalars(
        select(Block)
        .where(
            Block.conversation_id == root.conversation_id,
            Block.kind == BlockKind.message,
            Block.created_at < root.created_at,
        )
        .order_by(Block.created_at.desc(), Block.id.desc())
        .limit(limit)
    )
    return list(reversed(list(rows)))


async def _last_replies(
    session: AsyncSession, thread_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Block]:
    if not thread_ids:
        return {}
    ranked = (
        select(
            Block.id,
            func.row_number()
            .over(
                partition_by=Block.conversation_id,
                order_by=(Block.created_at.desc(), Block.id.desc()),
            )
            .label("rank"),
        )
        .where(
            Block.conversation_id == _among(thread_ids),
            Block.kind == BlockKind.message,
        )
        .subquery()
    )
    rows = await session.scalars(
        select(Block).join(ranked, ranked.c.id == Block.id).where(ranked.c.rank == 1)
    )
    return {block.conversation_id: block for block in rows}


def _reply(block: Block | None) -> dict | None:
    if block is None:
        return None
    return {
        "author": block.author,
        "content": block.content[:LAST_REPLY_CHARS],
        "created_at": block.created_at.isoformat(),
    }


def _summary(thread: Thread, last: Block | None) -> dict:
    return {
        "id": str(thread.id),
        "room_id": str(thread.room_id),
        "root_block_id": str(thread.root_block_id),
        "reply_count": thread.reply_count,
        "last_reply_at": thread.last_reply_at.isoformat()
        if thread.last_reply_at
        else None,
        "last_reply": _reply(last),
    }


async def under_messages(
    session: AsyncSession, block_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict]:
    """The line under each of these main-line messages that has a 支线 with a
    reply in it, keyed by the message."""
    if not block_ids:
        return {}
    threads = list(
        await session.scalars(
            select(Thread).where(
                Thread.root_block_id == _among(block_ids), Thread.reply_count > 0
            )
        )
    )
    last = await _last_replies(session, [t.id for t in threads])
    return {t.root_block_id: _summary(t, last.get(t.id)) for t in threads}


async def _participants(
    session: AsyncSession, threads: list[Thread]
) -> dict[uuid.UUID, list[str]]:
    """Who said something in each 支线, the message it hangs under first."""
    if not threads:
        return {}
    roots = {
        block.id: block.author
        for block in await session.scalars(
            select(Block).where(Block.id == _among([t.root_block_id for t in threads]))
        )
    }
    said: dict[uuid.UUID, list[str]] = {
        t.id: [roots[t.root_block_id]] if t.root_block_id in roots else []
        for t in threads
    }
    rows = await session.execute(
        select(Block.conversation_id, Block.author, func.min(Block.created_at))
        .where(
            Block.conversation_id == _among(said),
            Block.kind == BlockKind.message,
        )
        .group_by(Block.conversation_id, Block.author)
        .order_by(func.min(Block.created_at))
    )
    for conversation_id, author, _first in rows:
        if author not in said[conversation_id]:
            said[conversation_id].append(author)
    return said


async def _unread(
    session: AsyncSession, threads: list[Thread], viewer: str
) -> set[uuid.UUID]:
    """The 支线 with something new from another person since ``viewer`` last
    read them."""
    if not threads:
        return set()
    rows = await session.scalars(
        select(Block.conversation_id)
        .outerjoin(
            _reads,
            and_(
                _reads.c.topic_id == Block.conversation_id,
                _reads.c.user_handle == viewer,
            ),
        )
        .where(
            Block.conversation_id == _among([t.id for t in threads]),
            Block.kind == BlockKind.message,
            Block.author != viewer,
            Block.author_type == AuthorType.participant,
            ~agent_handle_column(Block.author),
            or_(
                _reads.c.last_read_at.is_(None),
                Block.created_at > _reads.c.last_read_at,
            ),
        )
        .distinct()
    )
    return set(rows)


async def in_room(
    session: AsyncSession, room_id: uuid.UUID, *, viewer: str | None, limit: int
) -> list[dict]:
    """A channel's 支线 that have replies, the latest reply first: the message
    each hangs under, its last reply, who took part, whether it became a task,
    and whether something new is waiting for ``viewer``."""
    threads = list(
        await session.scalars(
            select(Thread)
            .where(Thread.room_id == room_id, Thread.reply_count > 0)
            .order_by(Thread.last_reply_at.desc().nulls_last(), Thread.id)
            .limit(limit)
        )
    )
    if not threads:
        return []
    last = await _last_replies(session, [t.id for t in threads])
    roots = {
        block.id: block
        for block in await session.scalars(
            select(Block).where(Block.id == _among([t.root_block_id for t in threads]))
        )
    }
    tasks = {
        task.upgraded_from_block_id: task
        for task in await session.execute(
            select(
                _tasks.c.id,
                _tasks.c.title,
                _tasks.c.status,
                _tasks.c.upgraded_from_block_id,
            ).where(
                _tasks.c.upgraded_from_block_id
                == _among([t.root_block_id for t in threads])
            )
        )
    }
    said = await _participants(session, threads)
    unread = await _unread(session, threads, viewer) if viewer else set()
    out = []
    for thread in threads:
        root = roots.get(thread.root_block_id)
        people = said.get(thread.id, [])
        task = tasks.get(thread.root_block_id)
        out.append(
            {
                **_summary(thread, last.get(thread.id)),
                "root": _reply(root),
                "participants": people,
                "task": {
                    "id": str(task.id),
                    "title": task.title,
                    "status": task.status,
                }
                if task is not None
                else None,
                # Only the people who took part are told of a new reply, and
                # only of what people said in it.
                "unread": thread.id in unread
                and viewer in people
                and names_a_person(viewer),
            }
        )
    return out
