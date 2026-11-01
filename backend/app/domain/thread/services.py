"""Opening a 支线, and where a message to 芝士 is answered.

A message in a channel's main line that calls 芝士 is answered in that
message's 支线, never in the main line: the channel has no session of its own
for 芝士. A private chat keeps its own line, and a task's and a 支线's
messages are answered where they were said.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.block.models import Block, BlockKind
from app.domain.conversation.models import Conversation, ConversationKind
from app.domain.identity.handles import recipient_seat
from app.domain.thread.models import Thread
from app.domain.topic.models import Topic, is_private_chat, not_a_private_chat
from app.domain.topic_membership.services import TopicMemberService


async def _main_line_room(session: AsyncSession, block: Block):
    """The channel whose main line ``block`` is in, as ``(id, status)``; None
    when it is in a task, a 支线 or a private chat."""
    if isinstance(session, AsyncSession):
        cached = session.sync_session.identity_map.get(
            session.sync_session.identity_key(Topic, (block.conversation_id,))
        )
        if cached is not None:
            return None if is_private_chat(cached) else cached
    kind = await session.scalar(
        select(Conversation.kind).where(Conversation.id == block.conversation_id)
    )
    if kind != ConversationKind.room:
        return None
    # A private chat has no 支线: only a channel's main line does.
    return (
        await session.execute(
            select(Topic.id, Topic.status).where(
                Topic.id == block.conversation_id, not_a_private_chat()
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
    inserted = await session.scalar(
        insert(Thread)
        .values(
            id=uuid.uuid4(),
            project_id=block.project_id,
            room_id=room.id,
            root_block_id=block.id,
            created_by=by,
        )
        .on_conflict_do_nothing(index_elements=[Thread.root_block_id])
        .returning(Thread)
    )
    thread = inserted or await Thread.of_root(session, block.id)
    assert thread is not None
    return thread


async def thread_of(session: AsyncSession, thread_id: uuid.UUID) -> Thread | None:
    """The 支线 ``thread_id`` names, also once it became a task in place."""
    return await session.get(Thread, thread_id)


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
    return (await answered_in_many(session, [block]))[block.id]


async def _main_line_rooms(
    session: AsyncSession, conversation_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """`_main_line_room` for many conversations: the status of each channel among
    them, whose id is the key. What is not a channel is absent — a task, a 支线,
    a private chat, an id nothing names."""
    if not conversation_ids:
        return {}
    rooms = set(
        await session.scalars(
            select(Conversation.id).where(
                Conversation.id.in_(conversation_ids),
                Conversation.kind == ConversationKind.room,
            )
        )
    )
    if not rooms:
        return {}
    rows = await session.execute(
        select(Topic.id, Topic.status).where(Topic.id.in_(rooms), not_a_private_chat())
    )
    return {topic_id: status for topic_id, status in rows.all()}


async def _threads_under(
    session: AsyncSession, roots: Sequence[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """The 支线 each of these messages already has, keyed by its root block."""
    rows = await session.execute(
        select(Thread.root_block_id, Thread.id).where(Thread.root_block_id.in_(roots))
    )
    return {root: thread for root, thread in rows.all()}


async def answered_in_many(
    session: AsyncSession, blocks: Sequence[Block]
) -> dict[uuid.UUID, uuid.UUID]:
    """`answered_in` for a batch, reading each table it consults once.

    A message's answer place is its 支线 only when it was said in a channel's
    main line: the scan asks that of every waiting message, and all of them are
    about a handful of conversations. Same judgement as `answered_in`,
    including opening the 支线 a main-line message has none for — only the
    reads are shared.
    """
    blocks = list(blocks)
    if not blocks:
        return {}
    conversation_ids = {block.conversation_id for block in blocks}
    rooms = await _main_line_rooms(session, conversation_ids)
    opens = [
        block
        for block in blocks
        if block.kind == BlockKind.message
        and block.conversation_id in rooms
        and rooms[block.conversation_id] != "archived"
    ]
    threads: dict[uuid.UUID, uuid.UUID] = {}
    if opens:
        roots = [block.id for block in opens]
        threads = await _threads_under(session, roots)
        missing = [block for block in opens if block.id not in threads]
        if missing:
            await session.execute(
                insert(Thread)
                .values(
                    [
                        {
                            "id": uuid.uuid4(),
                            "project_id": block.project_id,
                            "room_id": block.conversation_id,
                            "root_block_id": block.id,
                            "created_by": block.author,
                        }
                        for block in missing
                    ]
                )
                .on_conflict_do_nothing(index_elements=[Thread.root_block_id])
            )
            # A 支线 another process opened first wins; read the ids back.
            threads = await _threads_under(session, roots)
    answers = {block.id: block.conversation_id for block in blocks}
    answers.update(threads)
    # A message that should have a 支线 has one now. `open_thread` asserts the
    # same thing; without it a write lost between the insert and the read would
    # answer in the channel's main line, where 芝士 has no session.
    assert all(block.id in threads for block in opens)
    return answers


async def thread_teammate(session: AsyncSession, thread: Thread) -> str | None:
    """The seat of the AI teammate a 支线 is talking with: the one the latest
    message that called a teammate there called, the message it hangs under
    included. None when nobody in it has called one, or when that teammate has
    since left the channel.

    A person's message said in a 支线 without calling anyone is for this
    teammate, and calls it as an @ would: the conversation is the one held with
    it, not with the channel's default."""
    root = (
        session.sync_session.identity_map.get(
            session.sync_session.identity_key(Block, (thread.root_block_id,))
        )
        if isinstance(session, AsyncSession) and not thread.reply_count
        else None
    )
    if root is not None and not any(
        isinstance(obj, Block) and obj.conversation_id == thread.id
        for obj in session.sync_session.identity_map.values()
    ):
        meta = (
            root.meta
            if root.kind == BlockKind.message
            and ((root.meta or {}).get("agent_recipient") or {}).get("mentioned")
            is True
            else None
        )
    else:
        meta = await session.scalar(
            select(Block.meta)
            .where(
                or_(
                    Block.conversation_id == thread.id,
                    Block.id == thread.root_block_id,
                ),
                Block.kind == BlockKind.message,
                Block.meta["agent_recipient"]["mentioned"].as_boolean(),
            )
            .order_by(Block.created_at.desc(), Block.id.desc())
            .limit(1)
        )
    seat = recipient_seat((meta or {}).get("agent_recipient"))
    if seat is None or seat not in await TopicMemberService(session).agent_handles(
        thread.room_id
    ):
        return None
    return seat


async def files_sent_with(session: AsyncSession, root: Block) -> list[Block]:
    """The files sent together with a message, in the order they were stored.

    A send with a message stores each file as an attachment block under that
    message's id (``turn_id``). An image-only send has no message: its first
    attachment is the root itself, and it has no files of its own.
    """
    if root.kind == BlockKind.attachment:
        return []
    rows = await session.scalars(
        select(Block)
        .where(
            Block.conversation_id == root.conversation_id,
            Block.turn_id == root.id,
            Block.kind == BlockKind.attachment,
        )
        .order_by(Block.created_at, Block.id)
    )
    return list(rows)


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
    return [root, *await files_sent_with(session, root)]


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


def onto_rooms(found: dict, rooms_of: dict[uuid.UUID, uuid.UUID]) -> dict:
    """What was found per 支线 (``found``, keyed by conversation) said of its
    channel too, where the channel has nothing of its own: a question 芝士 asks
    in a 支线 is the channel waiting on that person."""
    out = dict(found)
    for thread_id, room_id in rooms_of.items():
        if thread_id in found and room_id not in out:
            out[room_id] = found[thread_id]
    return out
