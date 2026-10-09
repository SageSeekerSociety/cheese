"""Who is in a channel (nested under /api/topics): reading the list, and its
managers adding and removing people and AI teammates. Joining and leaving by
oneself is in `topics_channel.py`."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.deps import get_chat_service
from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError
from app.domain.agent.chat import ChatService
from app.domain.agent.project_feed import TOPICS, tell_project
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import agent_instance_handle
from app.domain.identity.repositories import AgentBindingRepository
from app.domain.topic.services import TopicService
from app.domain.topic_membership.schemas import TopicMemberCreate, TopicMemberOut
from app.domain.topic_membership.services import TopicMemberService
from app.domain.user.repositories import UserProfileRepository, UserRepository

router = APIRouter(prefix="/topics", tags=["topic-members"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("/{topic_id}/members")
async def list_topic_members(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:

    # A task's id reaches its room's roster, files and documents.
    topic = (await TopicService(db).place_or_404(topic_id)).room
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    items = await roster_rows(db, topic)
    return ok(page(items, len(items)))


async def roster_rows(db: AsyncSession, topic) -> list[dict]:
    """A room's roster as `GET /topics/{id}/members` lists it; also the
    `members` of the room snapshot a subscription opens with (`room_snapshot`).
    The caller has authorised the reader."""
    seats = await TopicMemberService(db).seats(topic)
    # Attach display names and avatars so the UI can draw the roster without a
    # second round-trip.
    users = UserRepository(db)
    profiles = UserProfileRepository(db)
    bindings = AgentBindingRepository(db)
    # Resolve handles → users once, then derive is-agent from the binding (never
    # a hard-coded handle check): a member is an agent iff it carries a binding.
    rows = await users.get_by_handles([m.member_handle for m in seats])
    user_ids = [u.id for u in rows.values()]
    agent_ids = await bindings.agent_user_ids(user_ids)
    # The human-readable display name lives on the profile (nickname); the core
    # User row only carries the handle (username). Fall back to the handle.
    profile_by_uid = await profiles.get_profiles_by_user_ids(user_ids)
    avatar_by_uid = await profiles.chosen_avatar_ids(user_ids)
    # An agent seat's profile nickname is a constant fixed when the seat's user
    # row was created, so the name has to come from the agent. Each seat carries
    # its OWN agent's: a room may seat several, and one name for all of them
    # showed two teammates as the same person — the same defect the mention
    # roster had. A seat still under the room-derived handle belongs to the
    # agent the room points at, which is what `fallback_name` is.
    instances = {
        agent_instance_handle(instance.id): instance
        for instance in await AgentInstanceService(db).list_for_project(
            topic.project_id
        )
    }
    fallback = await TopicService(db).resolve_agent(topic)
    items = []
    for m in seats:
        d = m.model_dump(mode="json")
        user = rows.get(m.member_handle)
        profile = profile_by_uid.get(user.id) if user is not None else None
        # Agent members wear an Agent badge — derived from the execution binding.
        is_agent = user is not None and user.id in agent_ids
        d["agent"] = is_agent
        if is_agent:
            instance = instances.get(m.member_handle)
            agent = AgentInstanceService.resolved(instance) if instance else fallback
            d["name"] = agent.display_name
            d["name_source"] = agent.name_source.value
        else:
            d["name"] = (
                profile.nickname if profile and profile.nickname else m.member_handle
            )
        # Absent = this person never picked an avatar; the UI draws its coloured
        # initial rather than the one face everybody else who never picked has.
        d["avatar_id"] = avatar_by_uid.get(user.id) if user is not None else None
        items.append(d)
    return items


@router.post("/{topic_id}/members")
async def add_topic_member(
    topic_id: uuid.UUID,
    body: TopicMemberCreate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve(topic_id=topic_id)
    if not who.authenticated:
        raise AuthenticationRequiredError("A verified member identity is required")
    member = await TopicMemberService(db).add(
        topic_id=topic_id, handle=body.handle, actor=who.handle
    )
    await db.commit()
    # A private channel appears in the list of whoever was brought in.
    await tell_project(topic_id, TOPICS)
    return ok(TopicMemberOut.model_validate(member).model_dump(mode="json"))


@router.delete("/{topic_id}/members/{handle}")
async def remove_topic_member(
    topic_id: uuid.UUID,
    handle: str,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    who = await resolver.resolve(topic_id=topic_id)
    if not who.authenticated:
        raise AuthenticationRequiredError("A verified member identity is required")
    await TopicMemberService(db).remove(
        topic_id=topic_id, handle=handle, actor=who.handle
    )
    await db.commit()
    await chat.dismiss(topic_id, handle)
    # A private channel leaves the list of whoever was taken out.
    await tell_project(topic_id, TOPICS, also={handle})
    return ok({"deleted": True})
