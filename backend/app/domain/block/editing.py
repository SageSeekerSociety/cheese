"""Editing a message after it was sent.

One rule for every author: whoever sent a message may change its text, in the
room or on a card, and nobody else may. A person and an agent are the same
kind of member (docs/agent-principles.md 一/二), so there is no second path for
either; an agent's `todo_write` edits its checklist message through this same
function.

An edit does what sending the new text would have done, through the same
code: the text is rewritten as it would have been stored, whoever it newly
@s is notified, and a teammate it newly names gets a turn of its own — the
same delivery, on the same ledger, that a message naming it records (I13). The
room sees the edit as a `block_updated` frame carrying the whole block, whose
`meta.edited_at` it renders as 「已编辑」. An agent that already read the message
is told it changed, the way a message reaches it.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.block.authorship import is_participant
from app.domain.block.models import (
    AGENT_NOTICE_META_KEY,
    Block,
    BlockKind,
    consumed_turn,
    read_failed,
)
from app.domain.block.ports import Dispatcher, Notifier, RecordInstruction
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut, reply_preview

# The task an edit belongs to: its id and the room it is in. A bare table —
# `room_task` imports this domain, so reaching its service from here closes a
# C3 cycle.
from app.domain.block.task_rows import _tasks
from app.domain.conversation.services import is_task
from app.domain.delivery_events import DeliveryEvent, NotificationType
from app.domain.event_about import EventAbout, landing
from app.domain.event_author import AuthorType
from app.domain.notice_meta import (
    EVENT_MESSAGE_EDITED,
    SEVERITY_INFO,
    WHO_PLATFORM,
)
from app.domain.notice_meta import (
    notice as notice_meta,
)
from app.domain.run_record import service as run_records
from app.domain.run_record.models import RunRecord


class EditedText(Protocol):
    """Normalization facts the block edit consumes, not their room/seat resolver."""

    @property
    def text(self) -> str: ...

    @property
    def roster(self) -> list[dict]: ...

    @property
    def by_agent(self) -> bool: ...


_EffectSent = TypeVar("_EffectSent", bound=EditedText, contravariant=True)


class MentionEffects(Protocol[_EffectSent]):
    """What an edit does about the names in its new text, before it commits.

    Answers whether it started a teammate's turn: the caller lets the ledger out
    once the edit is committed and shown, the way sending does.
    """

    async def __call__(
        self,
        session: AsyncSession,
        sent: _EffectSent,
        block: Block,
        author: str,
        /,
        *,
        before: str,
    ) -> bool: ...


async def edit_message[Sent: EditedText](
    session: AsyncSession,
    publish: Callable[[str, dict], Awaitable[None]],
    block_id: uuid.UUID,
    *,
    editor: str,
    content: str,
    notify: Notifier,
    dispatch: Dispatcher,
    record_instruction: RecordInstruction,
    normalize_text: Callable[[AsyncSession, Block, str, str], Awaitable[Sent]],
    notify_mentions: MentionEffects[Sent],
    checklist: dict | None = None,
) -> dict:
    """Replace the text of ``editor``'s own message, commit, and tell the room.

    ``notify``、``dispatch``、``record_instruction`` 是本包够不着的那三步
    （`block/ports.py`）：说给跑着的那一轮听、把等着的投递放出去、给任务自己的
    席位留一句话。谁调用谁在门口递进来。

    ``checklist`` is `todo_write` editing its checklist message: the list
    travels with the text. Any other edit of that message leaves plain text."""
    blocks = BlockRepository(session)
    block = await blocks.get(block_id)
    if not _a_message(block):
        raise NotFoundError("Message not found")
    assert block is not None
    if block.author != editor:
        raise ForbiddenError("Only the author can edit this message")
    text = content.strip()
    if not text:
        raise ValidationError("content must not be blank")
    sent = await normalize_text(session, block, editor, text)
    before = block.content
    # Read and answered — or at least not failed after it was read: a message
    # whose reading ended in a failure was never answered, and editing it
    # asks it again.
    already_read = consumed_turn(block) is not None and read_failed(block) is None
    # A room's agent hears of an edit to a message it read; a task's message
    # was never the room's, and its own session is told through the ledger.
    in_room = not await is_task(session, block.conversation_id)
    await blocks.replace_content(block, sent.text, checklist=checklist)
    summoned = await notify_mentions(
        session,
        sent,
        block,
        editor,
        before=before,
    )
    notice = (
        await _tell_the_room(blocks, block, editor)
        if in_room and already_read
        else None
    )
    # Its frame is built now: once committed, the record's attributes expire.
    kept = (
        run_records.as_payload(await _keep_in_the_site(session, block, editor))
        if notice is not None
        else None
    )
    relayed = not in_room and not sent.by_agent
    if relayed:
        await _tell_the_card(
            session,
            block,
            block.conversation_id,
            editor,
            record_instruction=record_instruction,
        )
    payload = BlockOut.model_validate(block).model_dump(mode="json")
    # The frame replaces the line whole, so it carries what else is on it —
    # including the one-line preview a reply shows when its parent is not in the
    # reader's page.
    if block.reply_to is not None:
        parent = await blocks.get(block.reply_to)
        if parent is not None and parent.conversation_id == block.conversation_id:
            payload["reply_preview"] = reply_preview(parent)
    payload["reactions"] = await blocks.reactions_for_block(block.id)
    await session.commit()
    # A card's lines go out on the card's channel, where they are shown.
    await publish(
        str(block.conversation_id), {"type": "block_updated", "block": payload}
    )
    if kept is not None:
        await publish(
            str(block.conversation_id), {"type": run_records.FRAME, "record": kept}
        )
    if notice is not None:
        await notify.notify_running_session(
            block.conversation_id, _said(notice), blocks=[notice.id]
        )
    # What somebody else now has to run: a card's line relayed to its task, and
    # a teammate the new text named. Both wait in the ledger, and both are
    # picked up here — after the commit, so whoever wakes reads the new text.
    if relayed or summoned:
        await dispatch()
    return payload


def _a_message(block: Block | None) -> bool:
    """A participant's message, in a room or on a card: what can be edited."""
    return (
        block is not None
        and block.kind is BlockKind.message
        and is_participant(block.author_type)
    )


def _said(notice: Block) -> str:
    return str((notice.meta or {})[AGENT_NOTICE_META_KEY])


async def _tell_the_room(blocks: BlockRepository, block: Block, editor: str) -> Block:
    """The room's agent already read this message, so it is told it changed.

    Written as a notice to the agent, which is how the running turn hears of it
    now and, if nothing is running or the push misses, how the next turn reads
    it: pending until a turn stamps it. Not a line in the room — the room
    already shows the edit on the message itself."""
    landed = landing(
        EventAbout.room, project_id=block.project_id, room_id=block.conversation_id
    )
    return await blocks.add(
        project_id=landed.project_id,
        conversation_id=landed.conversation_id,
        author=editor,
        author_type=AuthorType.participant,
        content=say("messageEdited", actor=f"<@{editor}>"),
        kind=BlockKind.event,
        meta={
            "in_room": False,
            AGENT_NOTICE_META_KEY: (
                f"<@{editor}> 改了之前发的一条消息（id {block.id}），改后是：\n"
                f"{block.content}"
            ),
        },
    )


async def _keep_in_the_site(
    session: AsyncSession, block: Block, editor: str
) -> RunRecord:
    """The room's members see in 现场 that the agent was told of the edit.

    The notice block above is the agent's: it waits for a turn to read it. What
    a member reads is a run record of the same sentence, the way the platform's
    other running facts reach 现场."""
    return await run_records.keep(
        session,
        project_id=block.project_id,
        conversation_id=block.conversation_id,
        content=say("messageEdited", actor=f"<@{editor}>"),
        meta={
            **notice_meta(
                EVENT_MESSAGE_EDITED, severity=SEVERITY_INFO, who=WHO_PLATFORM
            ),
            "block_id": str(block.id),
        },
    )


async def _tell_the_card(
    session: AsyncSession,
    block: Block,
    task_id: uuid.UUID,
    editor: str,
    *,
    record_instruction: RecordInstruction,
) -> None:
    """What the owner says in a task reaches the task's own session; an edit
    goes the same way, saying it is one."""

    room_id = await session.scalar(
        select(_tasks.c.room_id).where(_tasks.c.id == task_id)
    )
    if room_id is None:
        return
    await record_instruction(
        session,
        DeliveryEvent(
            # Its own event: the message's id already names the relay of what
            # it first said, and the ledger keeps one delivery per event.
            id=uuid.uuid4(),
            type=NotificationType.ROOM_NOTICE,
            payload={"projectId": str(block.project_id), "topicId": str(room_id)},
            occurred_at=block.updated_at,
        ),
        task_id=task_id,
        content=(
            f"{editor} 改了之前说的一句（消息 id {block.id}），改后是：\n"
            f"{block.content}"
        ),
    )
