"""Who is in a channel (nested under /api/topics): reading the list, and its
managers adding and removing people and AI teammates. Joining and leaving by
oneself is in `topics_channel.py`.

The rows the list answers with are `app.api.topic_member_rows.roster_rows`,
which live outside this file: the room snapshot reads the same roster and is not
a route, so the builder sits under `app.api` where both may ask for it (C4
`routes-independent`)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.auth import ActorResolverDep
from app.api.deps import get_chat_service
from app.api.request_deps import DbSession
from app.api.response import ok, page
from app.api.topic_member_rows import roster_rows
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import AuthenticationRequiredError
from app.domain.agent.chat import ChatService
from app.domain.agent.own_calls import ask_owner_to_join
from app.domain.agent.project_feed import TOPICS, tell_project
from app.domain.agent_instance.adapters import project_team, seat_project
from app.domain.room_task.place import place_or_404
from app.domain.topic.services import TopicService
from app.domain.topic_membership.schemas import TopicMemberCreate, TopicMemberOut
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/topics", tags=["topic-members"])


@router.get("/{topic_id}/members")
async def list_topic_members(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:

    # A task's id reaches its room's roster, files and documents.
    topic = (await place_or_404(db, topic_id)).room
    await TopicMemberService(db)._repo.list_for_topic(topic.id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id, room=topic
    )
    viewer = actor.handle if actor.authenticated else None
    items = await roster_rows(db, topic, viewer=viewer)
    return ok(page(items, len(items)))


@router.post("/{topic_id}/members", dependencies=[ROUTE_DECIDES])
async def add_topic_member(
    topic_id: uuid.UUID,
    body: TopicMemberCreate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    topic = await TopicService(db).get(topic_id)
    who = await resolver.resolve(
        topic_id=topic_id, project_id=topic.project_id if topic is not None else None
    )
    if not who.authenticated:
        raise AuthenticationRequiredError("A verified member identity is required")
    if topic is not None:
        await TopicMemberService(db).list_for_topic(topic_id)
    member = await TopicMemberService(db).add(
        topic_id=topic_id,
        handle=body.handle,
        actor=who.handle,
        seat_project=seat_project,
        team=project_team,
    )
    await ask_owner_to_join(
        db, topic or (await place_or_404(db, topic_id)).room, body.handle
    )
    await db.commit()
    # A private channel appears in the list of whoever was brought in.
    await tell_project(topic_id, TOPICS)
    return ok(TopicMemberOut.model_validate(member).model_dump(mode="json"))


@router.delete("/{topic_id}/members/{handle}", dependencies=[ROUTE_DECIDES])
async def remove_topic_member(
    topic_id: uuid.UUID,
    handle: str,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    topic = await TopicService(db).get(topic_id)
    who = await resolver.resolve(
        topic_id=topic_id, project_id=topic.project_id if topic is not None else None
    )
    if not who.authenticated:
        raise AuthenticationRequiredError("A verified member identity is required")
    if topic is not None:
        await TopicMemberService(db).list_for_topic(topic_id)
    await TopicMemberService(db).remove(
        topic_id=topic_id, handle=handle, actor=who.handle
    )
    await db.commit()
    await chat.dismiss(topic_id, handle)
    # A private channel leaves the list of whoever was taken out.
    await tell_project(topic_id, TOPICS, also={handle})
    return ok({"deleted": True})
