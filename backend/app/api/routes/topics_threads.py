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
from app.api.topics_deps import BlockKind, BlockRepository, _actor_in_place
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    NotFoundError,
)
from app.core.sentences import say
from app.domain.agent.staleness import announce_stale
from app.domain.agent_instance.adapters import project_team
from app.domain.block.schemas import BlockOut
from app.domain.room_task.dispatch import say_thread_title
from app.domain.room_task.place import inner_place_or_404, place_or_404
from app.domain.thread import reads
from app.domain.thread.recycle import restore_thread as restore_a_thread
from app.domain.thread.schemas import ThreadOut
from app.domain.thread.services import (
    files_sent_with,
    open_thread,
    thread_of,
    thread_teammate,
)
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
        recycled_at=thread.recycled_at.isoformat() if thread.recycled_at else None,
    )


@block_router.post("/{block_id}/thread", dependencies=[ROUTE_DECIDES])
async def open_block_thread(
    block_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The 支线 under a message in a channel's main line, opened if it has
    none. A person opens it by replying there; 芝士's is opened for it when
    someone calls it in the main line."""
    conversation_id = await reads.said_in(db, block_id)
    if conversation_id is None:
        raise NotFoundError("Block not found")
    place = await place_or_404(db, conversation_id)
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
    place = await place_or_404(db, topic_id)
    actor = await _actor_in_place(resolver, place)
    if not actor.authenticated or actor.via == "cheese":
        raise ForbiddenError(say("threadOpenedByPerson"))
    if not await TopicMemberService(db).may_speak(
        place.room, actor.handle, team=project_team
    ):
        raise ForbiddenError(say("channelJoinToPost"))
    # 频道域把标题说在主线里，支线由这里开：`open_thread` 属于 `thread`，而
    # `thread` 在 `topic` 之上（C5 层表），频道域不能反向 import 它。
    block_id = await say_thread_title(
        db, room_id=place.room_id, title=body.title, by=actor.handle
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
    place = await place_or_404(db, topic_id)
    actor = await _actor_in_place(resolver, place)
    return ok(
        await reads.in_room(
            db,
            place.room_id,
            viewer=actor.handle if actor.authenticated else None,
            limit=limit,
        )
    )


@router.get("/{topic_id}/thread")
async def get_thread(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """One 支线, the message it hangs under, and the seat of the AI teammate
    it is talking with (`thread_teammate`; null before anyone called one): the
    one its composer offers to hand a message to. A 支线 that became a task
    is still one, and says so (``task``): the pane showing it shows the task's
    title, state and owner.

    Any other task reads the same way, opened beside the channel as a 支线 is:
    the message it hangs under is the one it was made from (a reply in a
    支线, or a main-line message), or none for a task created on its own."""
    place = await inner_place_or_404(db, topic_id)
    await _actor_in_place(resolver, place)
    thread = (
        place.thread
        if place.thread is not None
        else await thread_of(db, topic_id)
        if place.task is not None and place.task.upgraded_from_block_id is not None
        else None
    )
    if thread is None and place.task is not None:
        return ok(await _task_as_thread(db, place.task))
    if thread is None:
        raise NotFoundError(say("threadNotFound"))
    root = await reads.root_of(db, thread)
    files = await files_sent_with(db, root) if root is not None else []
    return ok(
        {
            # `_thread_out` 定型之后是模型，不是 dict：展开之前先 dump，别直接 `**`。
            **_thread_out(thread).model_dump(mode="json"),
            "root": BlockOut.model_validate(root).model_dump(mode="json")
            if root is not None
            else None,
            # The files sent together with the message it hangs under: they stay
            # in the main line, and the pane showing the 支线 draws them under
            # that message too.
            "files": [
                BlockOut.model_validate(f).model_dump(mode="json") for f in files
            ],
            "teammate": await thread_teammate(db, thread),
            "task": str(place.task.id) if place.task is not None else None,
        }
    )


@router.post(
    "/{topic_id}/thread/restore",
    dependencies=[ROUTE_DECIDES],
    **typed_response(ThreadOut),
)
async def restore_thread(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """把一条收起来的支线放回默认列表（`thread/recycle.py`）。

    收起来的支线是「报错满三天、没人回头」的那一条：它不再点亮频道，也从支线列表里
    挪到「已回收」一栏。内容一条没删，谁看得到这个频道谁就读得到它；谁能让它回到
    默认那一栏，是开这条支线的人和频道管理员 —— 和「谁能让一个频道重新排期」同一
    档人。

    放回来的是它「在哪一栏、算不算数」这两件事，不是那条失败：块没动过，那次坏掉
    已经过了时限，所以频道那一行的红灯不会跟着回来。那间房要再红，得是之后又有一次
    没人恢复的失败。再点一次「恢复」（本来就开着）也回 200：这件事本来就是幂等的。
    """
    place = await inner_place_or_404(db, topic_id)
    actor = await _actor_in_place(resolver, place)
    if not actor.authenticated:
        raise AuthenticationRequiredError(say("threadRestoreSignIn"))
    thread = place.thread
    if thread is None or place.task is not None:
        raise NotFoundError(say("threadNotFound"))
    if thread.recycled:
        managed = await TopicMemberService(db).managed_topic_ids(
            [place.room], actor.handle
        )
        if actor.handle != thread.created_by and place.room.id not in managed:
            raise ForbiddenError(say("threadRestoreNotYours"))
    await restore_a_thread(db, thread)
    out = _thread_out(thread).model_dump(mode="json")
    await db.commit()
    await announce_stale(place.room_id, "threads")
    return ok(out)


async def _task_as_thread(db, task) -> dict:
    """A task that never was a 支线, in the shape `get_thread` reads one."""
    origin = task.source_block_id or task.upgraded_from_block_id
    root = await BlockRepository(db).get(origin) if origin is not None else None
    files = await files_sent_with(db, root) if root is not None else []
    said = await BlockRepository(db).count_for_topic(task.id, kinds=[BlockKind.message])
    return {
        "id": str(task.id),
        "room_id": str(task.room_id),
        "root_block_id": str(root.id) if root is not None else None,
        "reply_count": said,
        "last_reply_at": None,
        "created_by": task.created_by,
        "created_at": task.created_at.isoformat(),
        "root": BlockOut.model_validate(root).model_dump(mode="json")
        if root is not None
        else None,
        "files": [BlockOut.model_validate(f).model_dump(mode="json") for f in files],
        "teammate": task.agent_handle,
        "task": str(task.id),
    }
