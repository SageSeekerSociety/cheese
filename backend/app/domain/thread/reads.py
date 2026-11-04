"""What a page shows of 支线: the line under each message in the main line,
and the channel's list of 支线.

Unread counts only for the people who took part — who wrote the message the
支线 hangs under or said something in it — and only what people said: the
same rule as a task's (`TopicRepository.unread_counts`). 芝士 answers in
every 支线 it is asked in, and a mark that lights at each answer is one nobody
reads.
"""

import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import (
    BigInteger,
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

from app.core.request_facts import recall, remember
from app.domain.block.indexed_rows import FAILED_TURN_ROWS
from app.domain.block.models import (
    CHECKLIST_META_KEY,
    Block,
    BlockKind,
)
from app.domain.block.repositories import BlockRepository
from app.domain.event_author import AuthorType
from app.domain.identity.handles import agent_handle_column, names_a_person
from app.domain.thread.models import Thread

# Bare tables: ``topic`` and ``room_task`` resolve 支线 (`room_task.place`), and
# importing them back would make a cycle.
_reads = table(
    "topic_read_states",
    column("topic_id", Uuid),
    column("user_handle", String),
    column("last_read_seq", BigInteger),
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


_ROUTINE_RUN_TABLES = frozenset({"routine_runs", "routines"})


def _can_cache(session: object) -> bool:
    return isinstance(session, AsyncSession)


async def routine_runs_of(
    session: AsyncSession, block_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict]:
    """Which of these messages stand for a run of a routine: the rule's
    title and its owner, keyed by the message."""
    if not block_ids:
        return {}
    if _can_cache(session):
        if session.autoflush:
            await session.flush()
        out: dict[uuid.UUID, dict] = {}
        missing: list[uuid.UUID] = []
        for bid in dict.fromkeys(block_ids):
            found, cached = recall(session, ("routine_run_owner_title", bid))
            if found:
                if cached is not None:
                    out[bid] = {"title": cached[0], "owner": cached[1]}
            else:
                missing.append(bid)
        if not missing:
            return out
        rows = await session.execute(
            select(_runs.c.message_id, _routines.c.title, _routines.c.owner_handle)
            .join(_routines, _routines.c.id == _runs.c.routine_id)
            .where(_runs.c.message_id == _among(missing))
        )
        fetched = {msg: (title, owner) for msg, title, owner in rows}
        for bid in missing:
            pair = fetched.get(bid)
            remember(
                session,
                ("routine_run_owner_title", bid),
                pair,
                tables=_ROUTINE_RUN_TABLES,
            )
            if pair is not None:
                out[bid] = {"title": pair[0], "owner": pair[1]}
        return out
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


def _became_a_task():
    """Whether the 支线 became a task in place: the task has its id."""
    return select(_tasks.c.id).where(_tasks.c.id == Thread.id).exists()


#: How much of the last reply the main line shows: two lines of it.
LAST_REPLY_CHARS = 200


async def said_in(session: AsyncSession, block_id: uuid.UUID) -> uuid.UUID | None:
    """The conversation a message was said in, or None when there is no such
    message."""
    block = await BlockRepository(session).get(block_id)
    return block.conversation_id if block is not None else None


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
        "author_type": block.author_type,
        "kind": block.kind,
        "content": block.content[:LAST_REPLY_CHARS],
        # A cut text is a preview, never the message: a reader drawing the
        # whole message from it (the 支线 header before its read returns) must
        # be able to tell.
        "truncated": len(block.content) > LAST_REPLY_CHARS,
        "created_at": block.created_at.isoformat(),
        "checklist": _checklist(block),
    }


def _checklist(block: Block) -> dict | bool | None:
    """The step checklist this message carries, or None when it carries none.

    The line under a message in the main line shows one step of the last
    reply, and its text cannot say which parts are steps: the line breaks that
    tell one step from the next are folded away on the way here, and the text
    is cut at ``LAST_REPLY_CHARS``. So the structure rides along with it — the
    same ``{"items", "result"}`` a block's ``meta.checklist`` holds. Messages
    written before that structure existed carry a bare ``true``, passed on as
    it is: their text is one marked step per line, and the line has to know
    that to read a step out of it rather than show the row of marks."""
    checklist = (block.meta or {}).get(CHECKLIST_META_KEY)
    if checklist is True:
        return True
    if not isinstance(checklist, dict) or not isinstance(checklist.get("items"), list):
        return None
    return checklist


async def _failed(
    session: AsyncSession, threads: list[Thread]
) -> dict[uuid.UUID, str | None]:
    """The 支线 whose last turn ended in an error after its last reply, each
    with the code of that failure (``credits_exhausted`` when the credits
    refused it), so the line under its message can say why.

    Only people's and AI teammates' messages count as replies, so a 支线 where
    the AI teammate's turn failed before it said anything has no reply and no
    one answering in it: without this its message would show nothing under it,
    and the reason the question went unanswered would sit unseen in the 支线."""
    if not threads:
        return {}
    rows = await session.execute(
        select(Block.conversation_id, Block.created_at, Block.meta["code"].as_string())
        .where(
            Block.conversation_id == _among([t.id for t in threads]),
            Block.kind == BlockKind.event,
            FAILED_TURN_ROWS,
        )
        .order_by(Block.created_at)
    )
    by_id = {t.id: t for t in threads}
    failed: dict[uuid.UUID, str | None] = {}
    for thread_id, at, code in rows:
        last_reply = by_id[thread_id].last_reply_at
        if last_reply is None or at > last_reply:
            failed[thread_id] = code
    return failed


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
        # 开这条支线的人（`threads.created_by`）：收起来之后能把它放回来的人之一
        # （`thread/recycle.py`）。不在 `participants` 里推 —— 那个列表是「谁说过
        # 话」，头一个是挂着的那条消息的作者，和「谁开的」是两回事。
        "created_by": thread.created_by,
        # 收起来的时刻（`thread/recycle.py`）；开着的是 None。
        "recycled_at": thread.recycled_at.isoformat() if thread.recycled_at else None,
    }


async def under_messages(
    session: AsyncSession,
    block_ids: list[uuid.UUID],
    *,
    replying: Callable[[list[uuid.UUID]], Awaitable[dict[uuid.UUID, list[str]]]]
    | None = None,
    roots: dict[uuid.UUID, Block] | None = None,
) -> dict[uuid.UUID, dict]:
    """The line under each of these main-line messages whose 支线 has a reply
    in it, an AI teammate answering in it now, or a turn that failed in it,
    keyed by the message: what `in_room` says of a 支线, and who is answering
    (``replying``: the seats with a turn running in each 支线 it is handed,
    asked only for the 支线 found here and not at all when there are none). A
    支线 that became a task keeps its line: the task's card is drawn on top of
    it, the same conversation with an owner."""
    if not block_ids:
        return {}
    found = list(
        await session.scalars(
            select(Thread).where(Thread.root_block_id == _among(block_ids))
        )
    )
    seats = (
        await replying([t.id for t in found]) if replying is not None and found else {}
    )
    failed = await _failed(session, [t for t in found if t.reply_count == 0])
    threads = [
        t for t in found if t.reply_count > 0 or seats.get(t.id) or t.id in failed
    ]
    rows = await _describe(session, threads, viewer=None, roots=roots)
    return {
        thread.root_block_id: {**row, "replying": seats.get(thread.id, [])}
        for thread, row in zip(threads, rows, strict=True)
    }


async def _participants(
    session: AsyncSession,
    threads: list[Thread],
    *,
    roots: dict[uuid.UUID, Block] | None = None,
) -> dict[uuid.UUID, list[str]]:
    """Who said something in each 支线, the message it hangs under first. A
    routine's run is a message of its teammate's, said on its owner's behalf:
    the owner takes part in that 支线 from the start.

    ``roots`` is the messages the 支线 hang under when the caller has already
    read them (`_describe` needs them whole), so the same rows are not read a
    second time; without it only their authors are read."""
    if not threads:
        return {}
    root_ids = [t.root_block_id for t in threads]
    runs = await routine_runs_of(session, root_ids)
    if roots is None:
        found = await session.execute(
            select(Block.id, Block.author).where(Block.id == _among(root_ids))
        )
        authors = {block_id: author for block_id, author in found}
    else:
        authors = {block_id: block.author for block_id, block in roots.items()}
    starters: dict[uuid.UUID, list[str]] = {}
    for block_id, author in authors.items():
        owner = runs.get(block_id, {}).get("owner")
        starters[block_id] = [author, *([owner] if owner else [])]
    said: dict[uuid.UUID, list[str]] = {
        t.id: list(starters.get(t.root_block_id, [])) for t in threads
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
                _reads.c.last_read_seq.is_(None),
                Block.seq > _reads.c.last_read_seq,
            ),
        )
        .distinct()
    )
    return set(rows)


async def in_room(
    session: AsyncSession, room_id: uuid.UUID, *, viewer: str | None, limit: int
) -> dict:
    """A channel's 支线, in two lists: the open ones, and the recycled ones.

    ``{"open": [...], "recycled": [...]}``. An open one has replies or a failed
    turn, latest first: the message each hangs under, its last reply, who took
    part, the tasks made from it, and whether something new is waiting for
    ``viewer``. One that became a task is listed with the channel's tasks, not
    here.

    A recycled one is a 支线 nobody came back to after it broke
    (`thread/recycle.py`): it has left the list it used to sit in and does not
    count towards the channel's red light, but it is still readable — nothing
    was deleted — so it is handed back in a list of its own, most recently
    recycled first. Its rows are the same shape plus ``recycled_at``.
    """
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
            .where(
                Thread.room_id == room_id,
                Thread.recycled_at.is_(None),
                or_(Thread.reply_count > 0, failed_turn),
                ~_became_a_task(),
            )
            .order_by(Thread.last_reply_at.desc().nulls_last(), Thread.id)
            .limit(limit)
        )
    )
    recycled = list(
        await session.scalars(
            select(Thread)
            .where(
                Thread.room_id == room_id,
                Thread.recycled_at.is_not(None),
                ~_became_a_task(),
            )
            .order_by(Thread.recycled_at.desc(), Thread.id)
            .limit(limit)
        )
    )
    return {
        "open": await _describe(session, threads, viewer=viewer),
        # 收起来的那几条一起读：它们和开着的一样有回复、有最后一句、有参与的人。
        "recycled": await _describe(session, recycled, viewer=None),
    }


async def _describe(
    session: AsyncSession,
    threads: list[Thread],
    *,
    viewer: str | None,
    roots: dict[uuid.UUID, Block] | None = None,
) -> list[dict]:
    """Each 支线 as a screen shows it, in the order given."""
    if not threads:
        return []
    last = await BlockRepository(session).last_messages([t.id for t in threads])
    if roots is None:
        roots = {
            block.id: block
            for block in await session.scalars(
                select(Block).where(
                    Block.id == _among([t.root_block_id for t in threads])
                )
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
    said = await _participants(session, threads, roots=roots)
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
                "failed_code": failed.get(thread.id),
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
