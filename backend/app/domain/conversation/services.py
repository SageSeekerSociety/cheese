"""What a conversation id answers: its project, the room it is in, and whether
it is a task's.

A conversation is a room, one of its tasks or one of its 支线, and its id is
that row's own (``conversations``). Requests and credentials name the conversation;
the roster, the work computer and the files are its room's.
"""

import uuid

from sqlalchemy import (
    ColumnElement,
    DateTime,
    Subquery,
    Uuid,
    any_,
    bindparam,
    column,
    func,
    select,
    table,
    union_all,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_facts import recall, remember
from app.domain.conversation.models import Conversation, ConversationKind

# Which room a task or a 支线 is in. Bare tables: ``room_task`` and ``thread``
# depend on this domain, and importing back would make a cycle.
_tasks = table("tasks", column("id", Uuid), column("room_id", Uuid))
_threads = table(
    "threads",
    column("id", Uuid),
    column("room_id", Uuid),
    column("recycled_at", DateTime(timezone=True)),
)
_inside = union_all(
    select(_tasks.c.id, _tasks.c.room_id), select(_threads.c.id, _threads.c.room_id)
).subquery("inside")

#: 一间房的支线，房间以**一个数组参数**进来。一次频道详情要问三趟同一批房间的支线
#: （`topics_signals.at_work`、`topics._awaiting_an_answer`、
#: `waits.MemberWaits.for_rooms`），三趟问的是同一件事。语句的形状与调用无关，于是
#: 建一次、处处复用；语句是只读的（执行不往它上面挂编译期状态），一份拿给并发用安全
#: —— `block/repositories.py` 的 `_awaiting_statements` 是同一个写法。
#:
#: 数组参数而不是 `IN` 列表：房间按百计，列表每个值绑一个参数，SQL 文本随长度变，
#: 每次调用都要重新编译一遍。
_threads_param = bindparam("threads_rooms", type_=ARRAY(Uuid))
# 收起来的支线不算：它不再点亮它那间频道的红灯，里面的题也不再折回频道
# （`thread/recycle.py`）。`recycled_at` 这一列因此两处都要读。
_room_threads = select(
    _threads.c.id,
    _threads.c.room_id,
    _threads.c.id.in_(
        select(Conversation.id).where(Conversation.kind == ConversationKind.thread)
    ),
).where(
    _threads.c.room_id == any_(_threads_param),
    _threads.c.recycled_at.is_(None),
)


async def project_of(
    session: AsyncSession, conversation_id: uuid.UUID
) -> uuid.UUID | None:
    return await session.scalar(
        select(Conversation.project_id).where(Conversation.id == conversation_id)
    )


async def is_task(session: AsyncSession, conversation_id: uuid.UUID) -> bool:
    kind = await session.scalar(
        select(Conversation.kind).where(Conversation.id == conversation_id)
    )
    return kind == ConversationKind.task


async def is_inner(session: AsyncSession, conversation_id: uuid.UUID) -> bool:
    """Whether the conversation is one inside a room — a task's or a 支线's —
    rather than a room's own line."""
    kind = await session.scalar(
        select(Conversation.kind).where(Conversation.id == conversation_id)
    )
    return kind in (ConversationKind.task, ConversationKind.thread)


async def room_of(session: AsyncSession, conversation_id: uuid.UUID) -> uuid.UUID:
    """The room a conversation is in: itself for a room, its room for a task
    or a 支线."""
    room = await session.scalar(
        select(_inside.c.room_id).where(_inside.c.id == conversation_id)
    )
    return room or conversation_id


async def rooms_of(
    session: AsyncSession, conversations: list[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """`room_of` for a batch in one query: each conversation mapped to its room,
    itself for a room. The reverse of `rooms_of_inner`, which starts from the
    rooms."""
    found = {conversation: conversation for conversation in conversations}
    if conversations:
        for inner_id, room_id in await session.execute(
            select(_inside.c.id, _inside.c.room_id).where(
                _inside.c.id.in_(conversations)
            )
        ):
            found[inner_id] = room_id
    return found


async def room_of_thread(
    session: AsyncSession, conversation_id: uuid.UUID
) -> uuid.UUID | None:
    """The room a 支线 is in, or ``None`` when the conversation is not one.

    Not `room_of`: that one answers a task's room too, and the id itself for a
    room, so a caller that has to tell a 支线 from everything else asks this."""
    return await session.scalar(
        select(_threads.c.room_id).where(_threads.c.id == conversation_id)
    )


async def rooms_of_inner(
    session: AsyncSession, rooms: list[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """Every conversation of these rooms — the rooms' own, their tasks' and
    their 支线' — mapped to its room."""
    found = {room: room for room in rooms}
    for inner_id, room_id in await session.execute(
        select(_inside.c.id, _inside.c.room_id).where(_inside.c.room_id.in_(rooms))
    ):
        found[inner_id] = room_id
    return found


async def _threads_in_rooms(
    session: AsyncSession, rooms: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, uuid.UUID], dict[uuid.UUID, uuid.UUID]]:
    """一间房的两份支线：全部，和「还当支线用」的那些。

    两份都跳过**收起来**的（`recycled_at` 非空，`thread/recycle.py`）：一条死掉的
    支线不再参与任何一行的红灯，里面的题、它给谁点过灯也不再折回频道。收起来只是
    不再算数，内容一条不删。
    """
    if not rooms:
        return {}, {}
    key = ("threads_in_rooms", tuple(rooms))
    found, cached = recall(session, key)
    if found:
        return cached
    rows = await session.execute(_room_threads, {"threads_rooms": list(rooms)})
    all_threads: dict[uuid.UUID, uuid.UUID] = {}
    still_threads: dict[uuid.UUID, uuid.UUID] = {}
    for thread_id, room_id, is_thread in rows:
        all_threads[thread_id] = room_id
        if is_thread:
            still_threads[thread_id] = room_id
    answer = (all_threads, still_threads)
    remember(session, key, answer)
    return answer


async def threads_of_rooms(
    session: AsyncSession, rooms: list[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """Every open 支线 of these rooms, mapped to its room.

    A room's tasks are not among them, unlike `rooms_of_inner`: a task is a
    board of its own with a card of its own, and what is said in one is not the
    room's own doings (`waits._last_said`). One that has been recycled is not
    either (`thread/recycle.py`): it is out of the row's units for good, until
    somebody restores it."""
    all_threads, _ = await _threads_in_rooms(session, rooms)
    return dict(all_threads)


async def threads_still_threads(
    session: AsyncSession, rooms: list[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """A room's 支线 that are still 支线, mapped to their room.

    Not `threads_of_rooms`, which reads the ``threads`` table alone: a 支线 that
    has become a task KEEPS its ``threads`` row (it is what hangs the
    conversation under its message, migration ``e4f7009313c1``), but its
    conversation is a task's now. Treating it as a 支线 again folds a task's
    failures back under the room — a closed piece of work relights its channel
    — and lets what is said in the task clear the room's own failures. The
    register (``conversations.kind``) is what tells the two apart.

    A recycled 支线 is not among them either: it was folded into the room's row
    until nobody came back to it, and folding it in again would put the room
    back the way the recycling was for."""
    _, still_threads = await _threads_in_rooms(session, rooms)
    return dict(still_threads)


def conversations_of(room_ids) -> Subquery:
    """Every conversation of these rooms — each room's own, its tasks' and its
    支线' — as rows of ``(id, room_id)``.

    The rooms travel as one array parameter, however many there are. A
    conversation is matched by membership in this one set rather than by three
    alternatives (``= room OR IN tasks OR IN 支线``): PostgreSQL cannot use an
    index on ``OR`` of ``IN`` subqueries, so that shape read the whole table
    the rows live in — every block ever said, for `MemberWaits` 1.5 s per
    `GET /topics` on dev (2026-10-08, 480k blocks)."""
    rooms = bindparam(None, list(room_ids), type_=ARRAY(Uuid))
    own = func.unnest(rooms).table_valued(column("id", Uuid)).render_derived()
    return union_all(
        select(own.c.id, own.c.id.label("room_id")),
        select(_tasks.c.id, _tasks.c.room_id).where(_tasks.c.room_id == any_(rooms)),
        select(_threads.c.id, _threads.c.room_id).where(
            _threads.c.room_id == any_(rooms)
        ),
    ).subquery("conversations_of")


def of_room(column, room_id: uuid.UUID) -> ColumnElement[bool]:
    """Rows of a room's conversations: the room's own and every task and
    支线 in it.

    For a room-wide question (the room's machine, its spend, its sessions).
    A question about one conversation compares ``column`` with its id."""
    return of_rooms(column, [room_id])


def of_rooms(column, room_ids) -> ColumnElement[bool]:
    """``of_room`` for several rooms at once."""
    return column.in_(select(conversations_of(room_ids).c.id))


def room_column(column) -> ColumnElement[uuid.UUID]:
    """The room a conversation id in ``column`` is in, as SQL: itself for a
    room, its room for a task or a 支线. For grouping rows by room."""
    return func.coalesce(
        select(_tasks.c.room_id).where(_tasks.c.id == column).scalar_subquery(),
        select(_threads.c.room_id).where(_threads.c.id == column).scalar_subquery(),
        column,
    )
