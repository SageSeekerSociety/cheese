"""What a conversation id answers: its project, the room it is in, and whether
it is a task's.

A conversation is a room or one of its tasks, and its id is the room's or the
task's own (``conversations``). Requests and credentials name the conversation;
the roster, the work computer and the files are its room's.
"""

import uuid

from sqlalchemy import Uuid, column, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.conversation.models import Conversation, ConversationKind

# Which room a task hangs in. A bare table: ``room_task`` depends on this
# domain, and importing back would make the two a cycle.
_tasks = table("tasks", column("id", Uuid), column("room_id", Uuid))


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


async def room_of(session: AsyncSession, conversation_id: uuid.UUID) -> uuid.UUID:
    """The room a conversation is in: itself for a room, its room for a task."""
    room = await session.scalar(
        select(_tasks.c.room_id).where(_tasks.c.id == conversation_id)
    )
    return room or conversation_id
