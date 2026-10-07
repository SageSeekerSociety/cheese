"""A project's channels from the project's side: the list 「浏览频道」 and the
settings' channels page read (`topic/directory.py`), and what whoever manages
the project may do to a channel without being in it: name its manager, or join
a private one, which is said in the channel so its people know. Archiving needs
no seat either (`POST /topics/{id}/archive`). Seeing what is said in a private
channel still takes a seat: the room door answers 404 to anyone else.
"""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.api.routes.projects import DbSession
from app.core.errors import AuthenticationRequiredError, NotFoundError
from app.core.sentences import say
from app.domain.agent.runtime import announce_stale
from app.domain.topic.directory import channel_directory
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/projects", tags=["projects"])


class ManagerIn(BaseModel):
    handle: str = Field(min_length=1, max_length=64)


async def _person(resolver, project_id: uuid.UUID):
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    if not actor.authenticated:
        raise AuthenticationRequiredError("A verified member identity is required")
    return actor


async def _channel(db, project_id: uuid.UUID, topic_id: uuid.UUID):
    topic = await TopicService(db).get_or_404(topic_id)
    if topic.project_id != project_id or topic.is_private:
        raise NotFoundError("Topic not found")
    return topic


@router.get("/{project_id}/channels")
async def list_channels(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The project's channels, archived ones too, as the reader may know them."""
    actor = await _person(resolver, project_id)
    return ok(await channel_directory(db, project_id, actor.handle))


@router.post("/{project_id}/channels/{topic_id}/step-in")
async def step_into_channel(
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Whoever manages the project joins a private channel they are not in,
    and the channel is told."""
    actor = await _person(resolver, project_id)
    topic = await _channel(db, project_id, topic_id)
    if await TopicMemberService(db).step_in(topic.id, actor.handle):
        await TopicService(db).note(
            topic,
            by=actor.handle,
            content=say("channelSteppedIn", actor=f"<@{actor.handle}>"),
        )
    await db.commit()
    await announce_stale(topic.id, "topics")
    return ok({"topic_id": str(topic.id), "joined": True})


@router.put("/{project_id}/channels/{topic_id}/manager")
async def hand_over_channel(
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    body: ManagerIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Name who manages this channel. Its manager or whoever manages the
    project may, the latter without being in it."""
    actor = await _person(resolver, project_id)
    topic = await _channel(db, project_id, topic_id)
    members = TopicMemberService(db)
    before = await members.owner_of(topic.id)
    await members.hand_over(topic.id, body.handle, actor=actor.handle)
    if before != body.handle:
        await TopicService(db).note(
            topic,
            by=actor.handle,
            content=say(
                "channelManagerChanged",
                actor=f"<@{actor.handle}>",
                manager=f"<@{body.handle}>",
            ),
        )
    await db.commit()
    await announce_stale(topic.id, "topics")
    return ok({"topic_id": str(topic.id), "manager": body.handle})
