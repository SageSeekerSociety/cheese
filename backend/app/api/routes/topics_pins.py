"""A channel's pins: what its members keep at the top of its overview.

Reading them is anyone who reads the channel; pinning and unpinning is anyone
who speaks in its main line (`app.domain.pin.services`). The module mounts
itself: `app.main._discover_routers` includes every module-level `APIRouter`
under `app.api.routes`.
"""

import uuid

from fastapi import APIRouter

from app.api.auth import ActorResolverDep
from app.api.deps import get_broker
from app.api.response import ok
from app.api.routes.topics import DbSession
from app.core.errors import AuthenticationRequiredError, NotFoundError
from app.domain.agent.runtime import announce_stale
from app.domain.block.schemas import BlockOut
from app.domain.pin.services import Pins
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/topics", tags=["topics"])


async def _channel(db, resolver, topic_id: uuid.UUID, *, acting: bool):
    """The channel and who is asking. A task or a 支线 has no pins."""
    place = await TopicService(db).place_or_404(topic_id)
    if place.conversation_id != place.room_id:
        raise NotFoundError("Topic not found")
    actor = await resolver.resolve(topic_id=topic_id, project_id=place.project_id)
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=topic_id
    )
    if acting and not actor.authenticated:
        raise AuthenticationRequiredError("A verified member identity is required")
    return place.room, actor


@router.get("/{topic_id}/pins")
async def list_pins(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The channel's pins, the latest pinned first, each with what it pins."""
    room, _ = await _channel(db, resolver, topic_id, acting=False)
    return ok(
        [
            {
                "pinned_by": pin.pinned_by,
                "pinned_at": pin.pinned_at.isoformat(),
                "block": BlockOut.model_validate(block).model_dump(mode="json"),
            }
            for pin, block in await Pins(db).of_room(room.id)
        ]
    )


@router.put("/{topic_id}/pins/{block_id}")
async def pin_block(
    topic_id: uuid.UUID,
    block_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Pin a message or a file of the channel's main line. The main line says
    who pinned what."""
    room, actor = await _channel(db, resolver, topic_id, acting=True)
    line = await Pins(db).pin(room, block_id, by=actor.handle)
    out = BlockOut.model_validate(line).model_dump(mode="json") if line else None
    await db.commit()
    if out is not None:
        await get_broker().publish(str(room.id), {"type": "event_block", "block": out})
    await announce_stale(room.id, "pins")
    return ok({"block_id": str(block_id), "pinned": True})


@router.delete("/{topic_id}/pins/{block_id}")
async def unpin_block(
    topic_id: uuid.UUID,
    block_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Take a pin off. Nothing is said in the main line."""
    room, actor = await _channel(db, resolver, topic_id, acting=True)
    await Pins(db).unpin(room, block_id, by=actor.handle)
    await db.commit()
    await announce_stale(room.id, "pins")
    return ok({"block_id": str(block_id), "pinned": False})
