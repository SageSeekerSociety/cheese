"""Being in a channel, and what it says about itself: joining, leaving, its
description, and whether it is private.

Everyone in a project reads every public channel; joining is what puts one in
a person's sidebar and lets them speak in its main line
(`TopicMemberService`). The module mounts itself: `app.main._discover_routers`
includes every module-level `APIRouter` under `app.api.routes`.
"""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.api.routes.topics import DbSession
from app.core.errors import AuthenticationRequiredError, NotFoundError
from app.domain.agent.project_feed import TOPICS, tell_project
from app.domain.agent.staleness import announce_stale
from app.domain.topic.schemas import TopicOut
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/topics", tags=["topics"])


async def _person(db, resolver, topic_id: uuid.UUID):
    """The channel and the signed-in person acting on it. A task or a 支线 is
    not a channel."""
    place = await TopicService(db).place_or_404(topic_id)
    if place.conversation_id != place.room_id:
        raise NotFoundError("Topic not found")
    actor = await resolver.resolve(topic_id=topic_id, project_id=place.project_id)
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=topic_id
    )
    if not actor.authenticated:
        raise AuthenticationRequiredError("A verified member identity is required")
    return place.room, actor


@router.post("/{topic_id}/join")
async def join_channel(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Join a public channel of a project I am in."""
    _, actor = await _person(db, resolver, topic_id)
    await TopicMemberService(db).join(topic_id, actor.handle)
    await db.commit()
    await announce_stale(topic_id, "topics", id=topic_id)
    return ok({"topic_id": str(topic_id), "joined": True})


@router.post("/{topic_id}/leave")
async def leave_channel(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Leave a channel. It stays readable; it leaves my sidebar, and I no
    longer speak in its main line."""
    _, actor = await _person(db, resolver, topic_id)
    await TopicMemberService(db).leave(topic_id, actor.handle)
    await db.commit()
    await announce_stale(topic_id, "topics", id=topic_id)
    return ok({"topic_id": str(topic_id), "joined": False})


@router.put("/{topic_id}/description")
async def set_description(
    topic_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """What the channel is for, set by whoever manages it. Empty clears it."""
    _, actor = await _person(db, resolver, topic_id)
    room = await TopicMemberService(db).require_manager(topic_id, actor.handle)
    room.description = str(body.get("description") or "").strip()[:500] or None
    await db.flush()
    out = TopicOut.model_validate(room).model_dump(mode="json")
    await db.commit()
    await announce_stale(topic_id, "topics", id=topic_id)
    return ok(out)


class MembersOnlyIn(BaseModel):
    members_only: bool


@router.put("/{topic_id}/members-only")
async def set_members_only(
    topic_id: uuid.UUID, body: MembersOnlyIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Make a channel private (its managers) or public again (whoever manages
    the project, from inside it). Whoever made it private stays in it."""
    _, actor = await _person(db, resolver, topic_id)
    room = await TopicMemberService(db).set_members_only(
        topic_id, body.members_only, actor=actor.handle
    )
    out = TopicOut.model_validate(room).model_dump(mode="json")
    await db.commit()
    await announce_stale(topic_id, "topics", id=topic_id)
    # Made private, it leaves the list of everyone not in it: they saw it a
    # moment ago, so telling them its id says nothing new.
    await tell_project(topic_id, TOPICS, everyone=True)
    return ok(out)
