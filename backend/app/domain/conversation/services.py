"""What a conversation id answers: its project, the room it is in, and whether
it is a task's.

A conversation is a room, one of its tasks or one of its 支线, and its id is
that row's own (``conversations``). Requests and credentials name the conversation;
the roster, the work computer and the files are its room's.
"""

import uuid

from sqlalchemy import ColumnElement, Uuid, column, func, or_, select, table, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

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


def of_room(
    column, room_id: uuid.UUID | ColumnElement[uuid.UUID] | InstrumentedAttribute
) -> ColumnElement[bool]:
    """Rows of a room's conversations: the room's own and every task and
    支线 in it.

    For a room-wide question (the room's machine, its spend, its sessions).
    A question about one conversation compares ``column`` with its id.
    ``room_id`` may be a column of the enclosing query (a correlated lookup)."""
    return or_(
        column == room_id,
        column.in_(
            select(_tasks.c.id)
            .where(_tasks.c.room_id == room_id)
            .correlate_except(_tasks)
        ),
        column.in_(
            select(_threads.c.id)
            .where(_threads.c.room_id == room_id)
            .correlate_except(_threads)
        ),
    )


def of_rooms(column, room_ids) -> ColumnElement[bool]:
    """``of_room`` for several rooms at once."""
    room_ids = list(room_ids)
    return or_(
        column.in_(room_ids),
        column.in_(select(_tasks.c.id).where(_tasks.c.room_id.in_(room_ids))),
        column.in_(select(_threads.c.id).where(_threads.c.room_id.in_(room_ids))),
    )


def room_column(column) -> ColumnElement[uuid.UUID]:
    """The room a conversation id in ``column`` is in, as SQL: itself for a
    room, its room for a task or a 支线. For grouping rows by room."""
    return func.coalesce(
        select(_tasks.c.room_id).where(_tasks.c.id == column).scalar_subquery(),
        select(_threads.c.room_id).where(_threads.c.id == column).scalar_subquery(),
        column,
    )
