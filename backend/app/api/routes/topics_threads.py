"""支线: the replies under a message in a channel.

`POST /blocks/{block}/thread` opens the 支线 under a main-line message (or
gives back the one it has), `POST /topics/{channel}/threads` opens one by
itself — a title, no message first (清单第 4 条) — `GET /topics/{channel}/threads`
lists a channel's 支线 for its overview, and `GET /topics/{thread}/thread` is
one 支线 and the message it hangs under. Talking in a 支线 and reading it go
through the same routes as a room's (`/messages`, `/blocks`, `/read`), with the
支线's id.

`_actor_in_place` comes from `topics_deps.py` and `DbSession` from
`request_deps.py`, as the other topics_* modules take them.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.topics_deps import _actor_in_place
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.agent.staleness import announce_stale
from app.domain.agent_instance.adapters import project_team
from app.domain.block.schemas import BlockOut
from app.domain.thread import reads
from app.domain.thread.schemas import ThreadOut
from app.domain.thread.services import open_thread, thread_teammate
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/topics", tags=["topics"])
block_router = APIRouter(prefix="/blocks", tags=["topics"])


class ThreadCreateIn(BaseModel):
    #: 支线的名字。手动开的支线总要先有话（`TopicService.say_thread_title` 把它说
    #: 在主线里），那句话就是它。
    title: str = Field(min_length=1, max_length=300)


def _thread_out(thread) -> ThreadOut:
    return ThreadOut(
        id=str(thread.id),
        room_id=str(thread.room_id),
        root_block_id=str(thread.root_block_id),
        reply_count=thread.reply_count,
        last_reply_at=thread.last_reply_at.isoformat()
        if thread.last_reply_at
        else None,
        created_by=thread.created_by,
        created_at=thread.created_at.isoformat(),
    )


@block_router.post("/{block_id}/thread")
async def open_block_thread(
    block_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The 支线 under a message in a channel's main line, opened if it has
    none. A person opens it by replying there; 芝士's is opened for it when
    someone calls it in the main line."""
    conversation_id = await reads.said_in(db, block_id)
    if conversation_id is None:
        raise NotFoundError("Block not found")
    place = await TopicService(db).place_or_404(conversation_id)
    actor = await _actor_in_place(resolver, place)
    if not actor.authenticated or actor.via == "cheese":
        raise ForbiddenError(say("threadOpenedByPerson"))
    thread = await open_thread(db, block_id, by=actor.handle)
    out = _thread_out(thread).model_dump(mode="json")
    await db.commit()
    await announce_stale(place.room_id, "threads")
    return ok(out)


@router.post(
    "/{topic_id}/threads",
    dependencies=[ROUTE_DECIDES],
    **typed_response(ThreadOut),
)
async def create_thread(
    topic_id: uuid.UUID,
    body: ThreadCreateIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """新建支线 (清单第 4 条): a 支线 opened from a channel by a person, which
    today is not possible — a 支线 needs a message to hang under, and the only
    way to one was replying to a message that was already said.

    A person opens it, as with the other road into a 支线: 芝士 does not open
    one by itself, and cats may not speak in a channel they have not joined.
    """
    place = await TopicService(db).place_or_404(topic_id)
    actor = await _actor_in_place(resolver, place)
    if not actor.authenticated or actor.via == "cheese":
        raise ForbiddenError(say("threadOpenedByPerson"))
    if not await TopicMemberService(db).may_speak(
        place.room, actor.handle, team=project_team
    ):
        raise ForbiddenError(say("channelJoinToPost"))
    # 频道域把标题说在主线里，支线由这里开：`open_thread` 属于 `thread`，而
    # `thread` 在 `topic` 之上（C5 层表），频道域不能反向 import 它。
    block_id = await TopicService(db).say_thread_title(
        room_id=place.room_id, title=body.title, by=actor.handle
    )
    thread = await open_thread(db, block_id, by=actor.handle)
    out = _thread_out(thread).model_dump(mode="json")
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
    """One 支线, the message it hangs under, and the seat of the AI teammate
    it is talking with (`thread_teammate`; null before anyone called one): the
    one its composer offers to hand a message to."""
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    if place.thread is None:
        raise NotFoundError("Thread not found")
    root = await reads.root_of(db, place.thread)
    return ok(
        {
            # `_thread_out` 定型之后是模型，不是 dict：展开之前先 dump，别直接 `**`。
            **_thread_out(place.thread).model_dump(mode="json"),
            "root": BlockOut.model_validate(root).model_dump(mode="json")
            if root is not None
            else None,
            "teammate": await thread_teammate(db, place.thread),
        }
    )
