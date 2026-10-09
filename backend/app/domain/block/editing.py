"""Editing a message after it was sent.

One rule for every author: whoever sent a message may change its text, in the
room or on a card, and nobody else may. A person and an agent are the same
kind of member (docs/agent-principles.md 一/二), so there is no second path for
either; an agent's `todo_write` edits its checklist message through this same
function.

An edit does what sending the new text would have done, through the same
code: the text is rewritten as it would have been stored, and whoever it newly
@s is notified. The room sees the edit as a `block_updated` frame carrying the
whole block, whose `meta.edited_at` it renders as 「已编辑」. An agent that
already read the message is told it changed, the way a message reaches it.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.block.about import EventAbout, landing
from app.domain.block.authorship import is_participant
from app.domain.block.models import (
    AGENT_NOTICE_META_KEY,
    AuthorType,
    Block,
    BlockKind,
    consumed_turn,
)
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.conversation.services import is_task
from app.domain.delivery.agent import dispatch_pending, record_task_instruction
from app.domain.delivery.ledger import DeliveryEvent
from app.domain.notification.models import NotificationType
from app.domain.room_task.services import TaskService

if TYPE_CHECKING:
    from app.domain.agent.chat import ChatService
    from app.domain.agent.runtime import AgentWorkRunner


class EditedText(Protocol):
    """Normalization facts the block edit consumes, not their room/seat resolver."""

    @property
    def text(self) -> str: ...

    @property
    def roster(self) -> list[dict] | None: ...

    @property
    def by_agent(self) -> bool: ...


_EffectSent = TypeVar("_EffectSent", bound=EditedText, contravariant=True)


class MentionEffects(Protocol[_EffectSent]):
    async def __call__(
        self,
        session: AsyncSession,
        sent: _EffectSent,
        block: Block,
        author: str,
        /,
        *,
        before: str,
    ) -> None: ...


async def edit_message[Sent: EditedText](
    session: AsyncSession,
    publish: Callable[[str, dict], Awaitable[None]],
    block_id: uuid.UUID,
    *,
    editor: str,
    content: str,
    chat: "ChatService",
    runner: "AgentWorkRunner",
    normalize_text: Callable[[AsyncSession, Block, str, str], Awaitable[Sent]],
    notify_mentions: MentionEffects[Sent],
    checklist: dict | None = None,
) -> dict:
    """Replace the text of ``editor``'s own message, commit, and tell the room.

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
    already_read = consumed_turn(block) is not None
    await blocks.replace_content(block, sent.text, checklist=checklist)
    if sent.roster is not None:
        await notify_mentions(
            session,
            sent,
            block,
            editor,
            before=before,
        )
    # A room's agent hears of an edit to a message it read; a task's message
    # was never the room's, and its own session is told through the ledger.
    in_room = not await is_task(session, block.conversation_id)
    notice = (
        await _tell_the_room(blocks, block, editor)
        if in_room and already_read
        else None
    )
    relayed = not in_room and not sent.by_agent
    if relayed:
        await _tell_the_card(session, block, block.conversation_id, editor)
    payload = BlockOut.model_validate(block).model_dump(mode="json")
    # The frame replaces the line whole, so it carries what else is on it.
    payload["reactions"] = await blocks.reactions_for_block(block.id)
    await session.commit()
    # A card's lines go out on the card's channel, where they are shown.
    await publish(
        str(block.conversation_id), {"type": "block_updated", "block": payload}
    )
    if notice is not None:
        await chat.notify_running_turn(
            block.conversation_id, _said(notice), blocks=[notice.id]
        )
    if relayed:
        await dispatch_pending(chat.session_factory, chat=chat, runner=runner)
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


async def _tell_the_card(
    session: AsyncSession, block: Block, task_id: uuid.UUID, editor: str
) -> None:
    """What the owner says in a task reaches the task's own session; an edit
    goes the same way, saying it is one."""

    task = await TaskService(session).get(task_id)
    if task is None:
        return
    await record_task_instruction(
        session,
        DeliveryEvent(
            # Its own event: the message's id already names the relay of what
            # it first said, and the ledger keeps one delivery per event.
            id=uuid.uuid4(),
            type=NotificationType.ROOM_NOTICE,
            payload={"projectId": str(block.project_id), "topicId": str(task.room_id)},
            occurred_at=block.updated_at,
        ),
        task=task,
        content=(
            f"{editor} 改了之前说的一句（消息 id {block.id}），改后是：\n"
            f"{block.content}"
        ),
    )
