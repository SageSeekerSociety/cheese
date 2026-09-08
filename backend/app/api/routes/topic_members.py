"""Topic membership routes (nested under /api/topics) — the group-room roster
(fusion-design §3)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError
from app.domain.topic.services import TopicService
from app.domain.topic_membership.schemas import (
    TopicMemberCreate,
    TopicMemberOut,
    TopicMemberRoleUpdate,
)
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/topics", tags=["topic-members"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("/{topic_id}/members")
async def list_topic_members(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    from app.domain.identity.repositories import AgentBindingRepository
    from app.domain.user.repositories import UserProfileRepository, UserRepository

    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    members, total = await TopicMemberService(db).list_for_topic(topic_id)
    # Attach display names and avatars so the UI can draw the roster without a
    # second round-trip.
    users = UserRepository(db)
    profiles = UserProfileRepository(db)
    bindings = AgentBindingRepository(db)
    # Resolve handles → users once, then derive is-agent from the binding (never
    # a hard-coded handle check): a member is an agent iff it carries a binding.
    rows = await users.get_by_handles([m.member_handle for m in members])
    user_ids = [u.id for u in rows.values()]
    agent_ids = await bindings.agent_user_ids(user_ids)
    # The human-readable display name lives on the profile (nickname); the core
    # User row only carries the handle (username). Fall back to the handle.
    profile_by_uid = await profiles.get_profiles_by_user_ids(user_ids)
    avatar_by_uid = await profiles.chosen_avatar_ids(user_ids)
    # 芝士's seat is one row per room whose profile nickname is a constant fixed
    # at creation (``IdentityService.ensure_topic_agent_user``), so it does NOT
    # follow the agent the room was handed to. Name that seat after whoever is
    # actually working here — the roster is where the UI reads the AI teammate's
    # name from, and a stale name there is indistinguishable from the swap not
    # having happened.
    agent_name = (await TopicService(db).resolve_agent(topic)).display_name
    items = []
    for m in members:
        d = TopicMemberOut.model_validate(m).model_dump(mode="json")
        user = rows.get(m.member_handle)
        profile = profile_by_uid.get(user.id) if user is not None else None
        # Agent members wear an Agent badge — derived from the execution binding.
        is_agent = user is not None and user.id in agent_ids
        d["agent"] = is_agent
        if is_agent:
            d["name"] = agent_name
        else:
            d["name"] = (
                profile.nickname if profile and profile.nickname else m.member_handle
            )
        # Absent = this person never picked an avatar; the UI draws its coloured
        # initial rather than the one face everybody else who never picked has.
        d["avatar_id"] = avatar_by_uid.get(user.id) if user is not None else None
        items.append(d)
    return ok(page(items, total))


@router.post("/{topic_id}/members")
async def add_topic_member(
    topic_id: uuid.UUID,
    body: TopicMemberCreate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve(fallback_handle=None, topic_id=topic_id)
    if not who.authenticated:
        raise AuthenticationRequiredError("A verified member identity is required")
    member = await TopicMemberService(db).add(
        topic_id=topic_id, handle=body.handle, role=body.role, actor=who.handle
    )
    await db.commit()
    return ok(TopicMemberOut.model_validate(member).model_dump(mode="json"))


@router.put("/{topic_id}/members/{handle}")
async def update_topic_member_role(
    topic_id: uuid.UUID,
    handle: str,
    body: TopicMemberRoleUpdate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve(fallback_handle=None, topic_id=topic_id)
    if not who.authenticated:
        raise AuthenticationRequiredError("A verified member identity is required")
    member = await TopicMemberService(db).update_role(
        topic_id=topic_id, handle=handle, role=body.role, actor=who.handle
    )
    await db.commit()
    return ok(TopicMemberOut.model_validate(member).model_dump(mode="json"))


@router.delete("/{topic_id}/members/{handle}")
async def remove_topic_member(
    topic_id: uuid.UUID,
    handle: str,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve(fallback_handle=None, topic_id=topic_id)
    if not who.authenticated:
        raise AuthenticationRequiredError("A verified member identity is required")
    await TopicMemberService(db).remove(
        topic_id=topic_id, handle=handle, actor=who.handle
    )
    await db.commit()
    return ok({"deleted": True})
