"""Block routes — emoji reactions (协作平台的消息表情, Slack semantics)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_broker
from app.api.response import ok
from app.core.db import get_db
from app.core.errors import NotFoundError
from app.domain.agent.runtime import InProcessBroker
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import ReactionToggleIn

router = APIRouter(prefix="/blocks", tags=["blocks"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.post("/{block_id}/reactions")
async def toggle_reaction(
    block_id: uuid.UUID,
    body: ReactionToggleIn,
    db: DbSession,
    broker: Annotated[InProcessBroker, Depends(get_broker)],
) -> dict:
    """Slack-style toggle: add the (emoji, author) reaction, or remove it when
    the same author reacts with the same emoji again. Broadcasts the block's
    fresh aggregate to the topic channel so every open client updates live."""
    repo = BlockRepository(db)
    block = await repo.get(block_id)
    if block is None:
        raise NotFoundError("Block not found")
    added = await repo.toggle_reaction(block_id, body.emoji, body.author)
    reactions = await repo.reactions_for_block(block_id)
    # Commit before broadcasting so a client that refetches on the frame
    # never reads stale state.
    await db.commit()
    await broker.publish(
        str(block.topic_id),
        {"type": "reaction", "block_id": str(block_id), "reactions": reactions},
    )
    return ok({"toggled": "added" if added else "removed", "reactions": reactions})
