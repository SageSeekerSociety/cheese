"""Block routes — editing a message, and emoji reactions (Slack semantics)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.deps import get_broker, get_chat_service, get_work_runner
from app.api.response import ok
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, NotFoundError
from app.domain.agent.chat import ChatService
from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.block.editing import edit_message
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import ReactionToggleIn
from app.domain.conversation.services import room_of

router = APIRouter(prefix="/blocks", tags=["blocks"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


class MessageEditIn(BaseModel):
    # The same ceiling as sending a message (`ChatMessageIn`).
    content: str = Field(min_length=1, max_length=100000)


@router.patch("/{block_id}")
async def edit_block(
    block_id: uuid.UUID,
    body: MessageEditIn,
    db: DbSession,
    resolver: ActorResolverDep,
    broker: Annotated[InProcessBroker, Depends(get_broker)],
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    """Edit a message you sent, in a room or on a card: a person with their
    session, an agent with its room credential — one route and one rule for
    both (`domain/block/editing`). Whoever is watching gets the edited message
    as a `block_updated` frame."""
    block = await BlockRepository(db).get(block_id)
    if block is None:
        raise NotFoundError("Message not found")
    actor = await resolver.resolve(
        topic_id=block.conversation_id, project_id=block.project_id
    )
    await resolver.authorize_topic(
        actor,
        project_id=block.project_id,
        topic_id=await room_of(db, block.conversation_id),
        enforce=True,
    )
    payload = await edit_message(
        db,
        broker.publish,
        block_id,
        editor=actor.handle,
        content=body.content,
        chat=chat,
        runner=runner,
    )
    return ok(payload)


@router.post("/{block_id}/reactions")
async def toggle_reaction(
    block_id: uuid.UUID,
    body: ReactionToggleIn,
    db: DbSession,
    resolver: ActorResolverDep,
    broker: Annotated[InProcessBroker, Depends(get_broker)],
) -> dict:
    """Slack-style toggle: add the (emoji, caller) reaction, or remove it when
    the same caller reacts with the same emoji again. Broadcasts the block's
    fresh aggregate to the topic channel so every open client updates live.

    A reaction is a write somebody does in a room, so it takes what any write
    there takes: a verified caller who can reach the block's room. The
    credential is judged against that room, because an agent's is bound to its
    project and answers only for a route that names one.

    The reaction lands under the caller the credential names — a session
    token's person or an agent credential's seat — and nobody else: the body
    carries no author. So the global sandbox token alone (the trusted dev
    credential, gate-only, naming nobody) gets a 401 here, since there is no one
    to put the reaction under."""
    repo = BlockRepository(db)
    block = await repo.get(block_id)
    if block is None:
        raise NotFoundError("Block not found")
    actor = await resolver.resolve(
        topic_id=block.conversation_id, project_id=block.project_id
    )
    await resolver.authorize_topic(
        actor,
        project_id=block.project_id,
        topic_id=await room_of(db, block.conversation_id),
        enforce=True,
    )
    if not actor.authenticated:
        raise AuthenticationRequiredError("Login required to react")
    added = await repo.toggle_reaction(block_id, body.emoji, actor.handle)
    reactions = await repo.reactions_for_block(block_id)
    # Commit before broadcasting so a client that refetches on the frame
    # never reads stale state.
    await db.commit()
    await broker.publish(
        str(block.conversation_id),
        {"type": "reaction", "block_id": str(block_id), "reactions": reactions},
    )
    return ok({"toggled": "added" if added else "removed", "reactions": reactions})
