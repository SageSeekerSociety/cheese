"""支线: the replies under a message in a channel.

`POST /blocks/{block}/thread` opens the 支线 under a main-line message (or
gives back the one it has), `GET /topics/{channel}/threads` lists a channel's
支线 for its overview, and `GET /topics/{thread}/thread` is one 支线 and the
message it hangs under. Talking in a 支线 and reading it go through the same
routes as a room's (`/messages`, `/blocks`, `/read`), with the 支线's id.

`_actor_in_place` and `DbSession` come from topics.py, as the other topics_*
modules take them.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.api.routes.topics import DbSession, _actor_in_place
from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.agent.runtime import announce_stale
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.thread import reads
from app.domain.thread.services import open_thread
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/topics", tags=["topics"])
block_router = APIRouter(prefix="/blocks", tags=["topics"])


def _thread_out(thread) -> dict:
    return {
        "id": str(thread.id),
        "room_id": str(thread.room_id),
        "root_block_id": str(thread.root_block_id),
        "reply_count": thread.reply_count,
        "last_reply_at": thread.last_reply_at.isoformat()
        if thread.last_reply_at
        else None,
        "created_by": thread.created_by,
        "created_at": thread.created_at.isoformat(),
    }


@block_router.post("/{block_id}/thread")
async def open_block_thread(
    block_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The 支线 under a message in a channel's main line, opened if it has
    none. A person opens it by replying there; 芝士's is opened for it when
    someone calls it in the main line."""
    block = await BlockRepository(db).get(block_id)
    if block is None:
        raise NotFoundError("Block not found")
    place = await TopicService(db).place_or_404(block.conversation_id)
    actor = await _actor_in_place(resolver, place)
    if not actor.authenticated or actor.via == "cheese":
        raise ForbiddenError(say("threadOpenedByPerson"))
    thread = await open_thread(db, block_id, by=actor.handle)
    out = _thread_out(thread)
    await db.commit()
    await announce_stale(place.room_id, "threads")
    return ok(out)


@router.get("/{topic_id}/threads")
async def list_threads(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    """A channel's 支线 with replies, the latest reply first."""
    place = await TopicService(db).place_or_404(topic_id)
    actor = await _actor_in_place(resolver, place)
    rows = await reads.in_room(
        db,
        place.room_id,
        viewer=actor.handle if actor.authenticated else None,
        limit=limit,
    )
    return ok(rows)


@router.get("/{topic_id}/thread")
async def get_thread(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """One 支线, and the message it hangs under."""
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    if place.thread is None:
        raise NotFoundError("Thread not found")
    root = await BlockRepository(db).get(place.thread.root_block_id)
    return ok(
        {
            **_thread_out(place.thread),
            "root": BlockOut.model_validate(root).model_dump(mode="json")
            if root is not None
            else None,
        }
    )
