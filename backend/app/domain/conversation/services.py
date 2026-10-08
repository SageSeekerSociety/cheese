"""What a conversation id answers: its project, the room it is in, and whether
it is a task's.

A conversation is a room, one of its tasks or one of its 支线, and its id is
that row's own (``conversations``). Requests and credentials name the conversation;
the roster, the work computer and the files are its room's.
"""

import uuid

from sqlalchemy import (
    ColumnElement,
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

from app.domain.conversation.models import Conversation, ConversationKind

# Which room a task or a 支线 is in. Bare tables: ``room_task`` and ``thread``
# depend on this domain, and importing back would make a cycle.
_tasks = table("tasks", column("id", Uuid), column("room_id", Uuid))
_threads = table("threads", column("id", Uuid), column("room_id", Uuid))
_inside = union_all(
    select(_tasks.c.id, _tasks.c.room_id), select(_threads.c.id, _threads.c.room_id)
).subquery("inside")


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
