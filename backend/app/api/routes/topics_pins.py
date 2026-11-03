"""A channel's pins: what its members keep at the top of its overview.

Reading them is anyone who reads the channel; pinning and unpinning is anyone
who speaks in its main line (`app.domain.pin.services`). The module mounts
itself: `app.main._discover_routers` includes every module-level `APIRouter`
under `app.api.routes`.

The list's rows themselves are `app.api.topic_pin_rows.pin_rows`: the room
snapshot embeds the same pins and is not a route, so the builder lives under
`app.api` where both may ask for it (C4 `routes-independent`).
"""

import uuid

from fastapi import APIRouter

from app.api.auth import ActorResolverDep
from app.api.deps import get_broker
from app.api.request_deps import DbSession
from app.api.response import ok
from app.api.topic_pin_rows import pin_rows
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import AuthenticationRequiredError, NotFoundError
from app.core.sentences import say
from app.domain.agent.staleness import announce_stale
from app.domain.agent_instance.adapters import project_team
from app.domain.block.schemas import BlockOut
from app.domain.pin.services import Pins
from app.domain.room_task.place import place_or_404

router = APIRouter(prefix="/topics", tags=["topics"])


async def _channel(db, resolver, topic_id: uuid.UUID, *, acting: bool):
    """The channel and who is asking. A task or a 支线 has no pins."""
    place = await place_or_404(db, topic_id)
    if place.conversation_id != place.room_id:
        raise NotFoundError(say("topicNotFound"))
    actor = await resolver.resolve(topic_id=topic_id, project_id=place.project_id)
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=topic_id, room=place.room
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
    return ok(await pin_rows(db, room.id))


@router.put("/{topic_id}/pins/{block_id}", dependencies=[ROUTE_DECIDES])
async def pin_block(
    topic_id: uuid.UUID,
    block_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Pin a message or a file of the channel's main line. The main line says
    who pinned what."""
    room, actor = await _channel(db, resolver, topic_id, acting=True)
    line = await Pins(db, team=project_team).pin(room, block_id, by=actor.handle)
    out = BlockOut.model_validate(line).model_dump(mode="json") if line else None
    await db.commit()
    if out is not None:
        await get_broker().publish(str(room.id), {"type": "event_block", "block": out})
    await announce_stale(room.id, "pins")
    return ok({"block_id": str(block_id), "pinned": True})


@router.delete("/{topic_id}/pins/{block_id}", dependencies=[ROUTE_DECIDES])
async def unpin_block(
    topic_id: uuid.UUID,
    block_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Take a pin off. Nothing is said in the main line."""
    room, actor = await _channel(db, resolver, topic_id, acting=True)
    await Pins(db, team=project_team).unpin(room, block_id, by=actor.handle)
    await db.commit()
    await announce_stale(room.id, "pins")
    return ok({"block_id": str(block_id), "pinned": False})
