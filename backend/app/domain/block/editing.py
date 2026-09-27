"""Editing a message after it was sent.

One rule for every author: whoever sent a message in a room may change its
text, and nobody else may. A person and an agent are the same kind of member
(docs/agent-principles.md 一/二), so there is no second path for either; an
agent's `todo_write` edits its checklist message through this same function.

The edit is shown to the room the way any change to a line already on screen
is: a `block_updated` frame carrying the whole block, whose `meta.edited_at`
the room renders as 「已编辑」.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.domain.agent.chat import text_as_sent
from app.domain.block.authorship import is_participant
from app.domain.block.models import Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut

if TYPE_CHECKING:
    from app.domain.agent.runtime import InProcessBroker


async def edit_message(
    session: AsyncSession,
    broker: "InProcessBroker",
    block_id: uuid.UUID,
    *,
    editor: str,
    content: str,
) -> dict:
    """Replace the text of ``editor``'s own message, commit, and tell the room."""
    blocks = BlockRepository(session)
    block = await blocks.get(block_id)
    if not _a_room_message(block):
        raise NotFoundError("Message not found")
    assert block is not None
    if block.author != editor:
        raise ForbiddenError("Only the author can edit this message")
    text = content.strip()
    if not text:
        raise ValidationError("content must not be blank")
    # Stored as sending this text would have stored it, by the same code.
    text = await text_as_sent(session, block.topic_id, editor, text)
    await blocks.replace_content(block, text)
    payload = BlockOut.model_validate(block).model_dump(mode="json")
    # The frame replaces the line whole, so it carries what else is on it.
    payload["reactions"] = await blocks.reactions_for_block(block.id)
    await session.commit()
    await broker.publish(
        str(block.topic_id), {"type": "block_updated", "block": payload}
    )
    return payload


def _a_room_message(block: Block | None) -> bool:
    """A participant's message on a room's own line: what can be edited."""
    return (
        block is not None
        and block.kind is BlockKind.message
        and is_participant(block.author_type)
        and block.task_id is None
    )
