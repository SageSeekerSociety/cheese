"""Opening a 支线, and where a message to 芝士 is answered.

A message in a channel's main line that calls 芝士 is answered in that
message's 支线, never in the main line: the channel has no session of its own
for 芝士. A private chat keeps its own line, and a task's and a 支线's
messages are answered where they were said.
"""

import uuid

from sqlalchemy import Boolean, String, Uuid, column, select, table
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.block.models import (
    CONSUMED_TURN_META_KEY,
    Block,
    BlockKind,
    consumed_turn,
)
from app.domain.conversation.models import Conversation, ConversationKind
from app.domain.conversation.services import of_room
from app.domain.thread.models import Thread

# The channel a message is in. A bare table: ``topic`` and ``room_task`` resolve
# 支线 (`room_task.place`), and importing them back would make a cycle.
_rooms = table(
    "topics",
    column("id", Uuid),
    column("is_private", Boolean),
    column("status", String),
)


async def _main_line_room(session: AsyncSession, block: Block):
    """The channel whose main line ``block`` is in, as ``(id, status)``; None
    when it is in a task, a 支线 or a private chat."""
    kind = await session.scalar(
        select(Conversation.kind).where(Conversation.id == block.conversation_id)
    )
    if kind != ConversationKind.room:
        return None
    # A private chat has no 支线: only a channel's main line does.
    return (
        await session.execute(
            select(_rooms.c.id, _rooms.c.status).where(
                _rooms.c.id == block.conversation_id, _rooms.c.is_private.is_(False)
            )
        )
    ).first()


async def open_thread(session: AsyncSession, block_id: uuid.UUID, *, by: str) -> Thread:
    """The 支线 under a message in a channel's main line, opened if it has
    none. Opening it twice, or by two people at once, gives the same one."""
    block = await session.get(Block, block_id)
    if block is None:
        raise NotFoundError("Block not found")
    room = await _main_line_room(session, block)
    if room is None or block.kind != BlockKind.message:
        raise ValidationError(say("threadOnlyUnderChannelMessage"))
    if room.status == "archived":
        raise ValidationError(say("roomArchivedUnarchiveFirst"))
    await session.execute(
        insert(Thread)
        .values(
            id=uuid.uuid4(),
            project_id=block.project_id,
            room_id=room.id,
            root_block_id=block.id,
            created_by=by,
        )
        .on_conflict_do_nothing(index_elements=[Thread.root_block_id])
    )
    thread = await Thread.of_root(session, block.id)
    assert thread is not None
    return thread


async def answer_place(
    sessions, block_id: uuid.UUID | None, posted_in: uuid.UUID
) -> uuid.UUID:
    """Where a message just posted in ``posted_in`` is answered
    (``answered_in``), in a transaction of its own: there, when it calls no
    one (``block_id`` None) or was not stored."""
    if block_id is None:
        return posted_in
    async with sessions() as session:
        block = await session.get(Block, block_id)
        if block is None:
            return posted_in
        conversation = await answered_in(session, block)
        await session.commit()
    return conversation


async def answered_in(session: AsyncSession, block: Block) -> uuid.UUID:
    """The conversation a message to 芝士 is answered in: its 支线 when it was
    said in a channel's main line, otherwise where it was said — and there too
    when its channel is archived, where no 支线 opens and the turn is refused
    as any turn in an archived channel is."""
    room = await _main_line_room(session, block)
    if room is None or room.status == "archived" or block.kind != BlockKind.message:
        return block.conversation_id
    return (await open_thread(session, block.id, by=block.author)).id


async def waiting_in_room(session: AsyncSession, room_id: uuid.UUID) -> list[uuid.UUID]:
    """The conversations of a room — its 支线 and tasks — where a message to
    芝士 is still waiting for an answer, oldest message first. A message still
    waiting in the main line is waiting in its 支线."""

    blocks = await session.scalars(
        select(Block)
        .where(
            of_room(Block.conversation_id, room_id),
            Block.kind == BlockKind.message,
            Block.meta["agent_recipient"]["mentioned"].as_boolean(),
        )
        .order_by(Block.created_at, Block.id)
    )
    waiting: list[uuid.UUID] = []
    for block in blocks:
        if CONSUMED_TURN_META_KEY not in (block.meta or {}):
            continue
        if consumed_turn(block) is not None:
            continue
        conversation = await answered_in(session, block)
        if conversation not in waiting:
            waiting.append(conversation)
    return waiting


async def thread_opening(
    session: AsyncSession, conversation_id: uuid.UUID
) -> list[Block]:
    """The message a 支线 hangs under and the files sent with it, when
    ``conversation_id`` is a 支线's; empty otherwise. They stay in the main
    line, and they are the 支线's first input all the same."""
    root_id = await session.scalar(
        select(Thread.root_block_id).where(Thread.id == conversation_id)
    )
    if root_id is None:
        return []
    root = await session.get(Block, root_id)
    if root is None:
        return []
    files = await session.scalars(
        select(Block)
        .where(
            Block.conversation_id == root.conversation_id,
            Block.turn_id == root.id,
            Block.kind == BlockKind.attachment,
        )
        .order_by(Block.created_at, Block.id)
    )
    return [root, *files]


async def conversation_inputs(
    session: AsyncSession, conversation_id: uuid.UUID
) -> list[Block]:
    """Everything said in a conversation, oldest first, with a 支线's message
    and its files in front: what its unread inputs are read from."""
    blocks = await session.scalars(
        select(Block)
        .where(Block.conversation_id == conversation_id)
        .order_by(Block.created_at, Block.id)
    )
    return [*await thread_opening(session, conversation_id), *blocks]


async def threads_of_rooms(
    session: AsyncSession, room_ids: list[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """Every 支线 of these channels, mapped to its channel."""
    if not room_ids:
        return {}
    rows = await session.execute(
        select(Thread.id, Thread.room_id).where(Thread.room_id.in_(room_ids))
    )
    return {thread_id: room_id for thread_id, room_id in rows}


def onto_rooms(found: dict, rooms_of: dict[uuid.UUID, uuid.UUID]) -> dict:
    """What was found per 支线 (``found``, keyed by conversation) said of its
    channel too, where the channel has nothing of its own: a question 芝士 asks
    in a 支线 is the channel waiting on that person."""
    out = dict(found)
    for thread_id, room_id in rooms_of.items():
        if thread_id in found and room_id not in out:
            out[room_id] = found[thread_id]
    return out
