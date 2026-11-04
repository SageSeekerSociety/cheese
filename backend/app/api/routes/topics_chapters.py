"""A conversation's chapters: the messages that start a section of it.

Reading them is anyone who reads the conversation; marking and unmarking is
anyone who may speak where the section starts (`app.domain.chapter.services`).
A task and a 支线 are addressed by their own ids, like their messages are. The
module mounts itself: `app.main._discover_routers` includes every module-level
`APIRouter` under `app.api.routes`.

The list's rows are `app.api.chapter_rows`: the room snapshot embeds the same
chapters and is not a route, so the builder lives under `app.api` where both may
ask for it (C4 `routes-independent`).
"""

import uuid

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from app.api.auth import ActorResolverDep
from app.api.chapter_rows import chapter_rows
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import AuthenticationRequiredError
from app.core.sentences import say
from app.domain.agent.staleness import announce_stale
from app.domain.agent_instance.adapters import project_team
from app.domain.chapter import naming
from app.domain.chapter.schemas import ChapterMarkOut, ChapterOut, ChapterTitleOut
from app.domain.chapter.services import Chapters
from app.domain.room_task.place import place_or_404

router = APIRouter(prefix="/topics", tags=["topics"])


async def _conversation(db, resolver, conversation_id: uuid.UUID, *, acting: bool):
    """The conversation and who is asking — a channel's main line, a task or a
    支线 (all three are conversations the reader may see)."""
    place = await place_or_404(db, conversation_id)
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, room=place.room
    )
    if acting and not actor.authenticated:
        raise AuthenticationRequiredError(say("signInRequired"))
    return place, actor


@router.get("/{topic_id}/chapters", **typed_response(list[ChapterOut]))
async def list_chapters(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The conversation's chapters, in reading order (the order they are
    numbered in)."""
    place, _ = await _conversation(db, resolver, topic_id, acting=False)
    return ok(await chapter_rows(db, place.conversation_id))


async def _refine_name(conversation_id: uuid.UUID, block_id: uuid.UUID) -> None:
    if await naming.refine(conversation_id, block_id):
        await announce_stale(conversation_id, "chapters")


@router.put(
    "/{topic_id}/chapters/{block_id}",
    dependencies=[ROUTE_DECIDES],
    **typed_response(ChapterMarkOut),
)
async def add_chapter(
    topic_id: uuid.UUID,
    block_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    background: BackgroundTasks,
) -> dict:
    """Mark one of this conversation's messages as the start of a chapter. Says
    nothing in the conversation; marking it twice changes nothing.

    The name is read off the message at once and the model gets its own short
    look after this response (`app.domain.chapter.naming`): the click must not
    wait for a model, and a failed look leaves the name it already has.
    """
    place, actor = await _conversation(db, resolver, topic_id, acting=True)
    added = await Chapters(db, team=project_team).add(place, block_id, by=actor.handle)
    await db.commit()
    if added:
        await announce_stale(place.conversation_id, "chapters")
        background.add_task(_refine_name, place.conversation_id, block_id)
    return ok({"block_id": str(block_id), "chapter": True})


class ChapterTitleIn(BaseModel):
    title: str


@router.patch(
    "/{topic_id}/chapters/{block_id}",
    dependencies=[ROUTE_DECIDES],
    **typed_response(ChapterTitleOut),
)
async def rename_chapter(
    topic_id: uuid.UUID,
    block_id: uuid.UUID,
    body: ChapterTitleIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Give a bookmark a name of one's own. A name somebody typed is final: the
    model's later look does not overwrite it."""
    place, actor = await _conversation(db, resolver, topic_id, acting=True)
    title = await Chapters(db, team=project_team).rename(
        place, block_id, body.title, by=actor.handle
    )
    await db.commit()
    await announce_stale(place.conversation_id, "chapters")
    return ok({"block_id": str(block_id), "title": title})


@router.delete(
    "/{topic_id}/chapters/{block_id}",
    dependencies=[ROUTE_DECIDES],
    **typed_response(ChapterMarkOut),
)
async def remove_chapter(
    topic_id: uuid.UUID,
    block_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Take a chapter off. Quiet, and nothing when it was not one."""
    place, actor = await _conversation(db, resolver, topic_id, acting=True)
    await Chapters(db, team=project_team).remove(place, block_id, by=actor.handle)
    await db.commit()
    await announce_stale(place.conversation_id, "chapters")
    return ok({"block_id": str(block_id), "chapter": False})
