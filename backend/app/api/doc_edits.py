"""Which turn a change to a document belongs to.

An AI teammate edits a document the way a person does: its passage edits are
made directly and recorded as its own. It may choose to propose them instead
(``mode: "suggest"``, the ``suggest`` flag of its edit tool), leaving each as a
suggestion a person accepts or rejects; nothing forces that choice on it.

What is looked up here is only where the conversation's line about the change
belongs: inside the running turn whose tool call made it.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.announce import instance_of_seat
from app.domain.agent.repositories import AgentTurnRepository


async def edit_turn(
    db: AsyncSession, *, project_id: uuid.UUID, conversation_id: uuid.UUID, actor: str
) -> uuid.UUID | None:
    """The running turn of ``actor`` (an AI teammate's seat) in
    ``conversation_id``: the turn whose tool call made the change, which the
    conversation's line about it is part of. A turn row names the agent by
    its instance's handle, a seat by the instance's id, so both are asked.
    None for a person (no turn is ever a person's), or an agent with no turn
    open there."""
    instance = await instance_of_seat(db, project_id, actor)
    handles = [actor, *((instance.handle,) if instance is not None else ())]
    turn = await AgentTurnRepository(db).open_turn_of(conversation_id, handles)
    return turn.id if turn is not None else None
