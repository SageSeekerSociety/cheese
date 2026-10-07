"""What a page shows of 支线: the line under each message in the main line,
and the channel's list of 支线.

Unread counts only for the people who took part — who wrote the message the
支线 hangs under or said something in it — and only what people said: the
same rule as a task's (`TopicRepository.unread_counts`). 芝士 answers in
every 支线 it is asked in, and a mark that lights at each answer is one nobody
reads.
"""

import uuid
from collections.abc import Callable

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

from app.domain.block.indexed_rows import FAILED_TURN_ROWS
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
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
    column("created_at", DateTime(timezone=True)),
)


# A routine's run is a message in the main line. Bare tables: ``routine``
# depends on this domain.
_runs = table("routine_runs", column("message_id", Uuid), column("routine_id", Uuid))
_routines = table(
    "routines",
    column("id", Uuid),
    column("title", String),
    column("owner_handle", String),
)


async def routine_runs_of(
    session: AsyncSession, block_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict]:
    """Which of these messages stand for a run of a routine: the rule's
    title and its owner, keyed by the message."""
    if not block_ids:
        return {}
    rows = await session.execute(
        select(_runs.c.message_id, _routines.c.title, _routines.c.owner_handle)
        .join(_routines, _routines.c.id == _runs.c.routine_id)
        .where(_runs.c.message_id == _among(block_ids))
    )
    return {message: {"title": title, "owner": owner} for message, title, owner in rows}


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


def _reply(block: Block | None) -> dict | None:
    if block is None:
        return None
    return {
        "author": block.author,
        "content": block.content[:LAST_REPLY_CHARS],
        "created_at": block.created_at.isoformat(),
    }


async def _failed(session: AsyncSession, threads: list[Thread]) -> set[uuid.UUID]:
    """The 支线 whose last turn ended in an error after its last reply.

    Only people's and AI teammates' messages count as replies, so a 支线 where
    the AI teammate's turn failed before it said anything has no reply and no
    one answering in it: without this its message would show nothing under it,
    and the reason the question went unanswered would sit unseen in the 支线."""
    if not threads:
        return set()
    rows = await session.execute(
        select(Block.conversation_id, func.max(Block.created_at))
        .where(
            Block.conversation_id == _among([t.id for t in threads]),
            Block.kind == BlockKind.event,
            FAILED_TURN_ROWS,
        )
        .group_by(Block.conversation_id)
    )
    by_id = {t.id: t for t in threads}
    return {
        thread_id
        for thread_id, at in rows
        if by_id[thread_id].last_reply_at is None or at > by_id[thread_id].last_reply_at
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
    session: AsyncSession,
    block_ids: list[uuid.UUID],
    *,
    replying: Callable[[uuid.UUID], list[str]] = lambda _thread: [],
) -> dict[uuid.UUID, dict]:
    """The line under each of these main-line messages whose 支线 has a reply
    in it, an AI teammate answering in it now, or a turn that failed in it,
    keyed by the message: what `in_room` says of a 支线, and who is answering
    (``replying``: the seats with a turn running in a 支线)."""
    if not block_ids:
        return {}
    found = list(
        await session.scalars(
            select(Thread).where(Thread.root_block_id == _among(block_ids))
        )
    )
    failed = await _failed(session, [t for t in found if t.reply_count == 0])
    threads = [
        t for t in found if t.reply_count > 0 or replying(t.id) or t.id in failed
    ]
    rows = await _describe(session, threads, viewer=None)
    return {
        thread.root_block_id: {**row, "replying": replying(thread.id)}
        for thread, row in zip(threads, rows, strict=True)
    }


async def _participants(
    session: AsyncSession, threads: list[Thread]
) -> dict[uuid.UUID, list[str]]:
    """Who said something in each 支线, the message it hangs under first. A
    routine's run is a message of its teammate's, said on its owner's behalf:
    the owner takes part in that 支线 from the start."""
    if not threads:
        return {}
    root_ids = [t.root_block_id for t in threads]
    runs = await routine_runs_of(session, root_ids)
    roots: dict[uuid.UUID, list[str]] = {}
    for block in await session.scalars(
        select(Block).where(Block.id == _among(root_ids))
    ):
        owner = runs.get(block.id, {}).get("owner")
        roots[block.id] = [block.author, *([owner] if owner else [])]
    said: dict[uuid.UUID, list[str]] = {
        t.id: list(roots.get(t.root_block_id, [])) for t in threads
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


async def people_in(session: AsyncSession, thread_id: uuid.UUID) -> list[str]:
    """The people who took part in a 支线: who wrote the message it hangs under
    and who said something in it. Empty when ``thread_id`` is no 支线."""
    thread = await session.get(Thread, thread_id)
    if thread is None:
        return []
    said = (await _participants(session, [thread])).get(thread.id, [])
    return [h for h in said if names_a_person(h)]


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
    """A channel's 支线 that have replies or a failed turn, the latest first:
    the message each hangs under, its last reply, who took part, whether it
    became a task, and whether something new is waiting for ``viewer``."""
    failed_turn = (
        select(Block.id)
        .where(
            Block.conversation_id == Thread.id,
            Block.kind == BlockKind.event,
            FAILED_TURN_ROWS,
        )
        .exists()
    )
    threads = list(
        await session.scalars(
            select(Thread)
            .where(Thread.room_id == room_id, or_(Thread.reply_count > 0, failed_turn))
            .order_by(Thread.last_reply_at.desc().nulls_last(), Thread.id)
            .limit(limit)
        )
    )
    if not threads:
        return []
    return await _describe(session, threads, viewer=viewer)


async def _describe(
    session: AsyncSession, threads: list[Thread], *, viewer: str | None
) -> list[dict]:
    """Each 支线 as a screen shows it, in the order given."""
    if not threads:
        return []
    last = await BlockRepository(session).last_messages([t.id for t in threads])
    roots = {
        block.id: block
        for block in await session.scalars(
            select(Block).where(Block.id == _among([t.root_block_id for t in threads]))
        )
    }
    tasks: dict[uuid.UUID, list] = {}
    for task in await session.execute(
        select(
            _tasks.c.id,
            _tasks.c.title,
            _tasks.c.status,
            _tasks.c.upgraded_from_block_id,
        )
        .where(
            _tasks.c.upgraded_from_block_id
            == _among([t.root_block_id for t in threads])
        )
        .order_by(_tasks.c.created_at)
    ):
        tasks.setdefault(task.upgraded_from_block_id, []).append(task)
    said = await _participants(session, threads)
    unread = await _unread(session, threads, viewer) if viewer else set()
    failed = await _failed(session, threads)
    out = []
    for thread in threads:
        root = roots.get(thread.root_block_id)
        people = said.get(thread.id, [])
        out.append(
            {
                **_summary(thread, last.get(thread.id)),
                "root": _reply(root),
                "participants": people,
                "failed": thread.id in failed,
                # The tasks made from the message or its 支线, oldest first.
                "tasks": [
                    {"id": str(task.id), "title": task.title, "status": task.status}
                    for task in tasks.get(thread.root_block_id, [])
                ],
                # Only the people who took part are told of a new reply, and
                # only of what people said in it.
                "unread": thread.id in unread
                and viewer in people
                and names_a_person(viewer),
            }
        )
    return out
