"""Topic membership routes (nested under /api/topics) — the group-room roster
(fusion-design §3).

No auth dependency exists yet (agent-as-user is P1): the acting user's handle
is passed explicitly (`actor` in the body, or the `actor` query param on
DELETE) and the service authorizes against their topic role — owner/admin may
manage the roster, plain members may not.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok, page
from app.core.db import get_db
from app.domain.topic_membership.schemas import (
    TopicMemberCreate,
    TopicMemberOut,
    TopicMemberRoleUpdate,
)
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/api/topics", tags=["topic-members"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("/{topic_id}/members")
async def list_topic_members(topic_id: uuid.UUID, db: DbSession) -> dict:
    from app.domain.identity.repositories import AgentBindingRepository
    from app.domain.user.repositories import UserRepository

    members, total = await TopicMemberService(db).list_for_topic(topic_id)
    # Attach display names so the UI can label 头像 without a second round-trip.
    users = UserRepository(db)
    bindings = AgentBindingRepository(db)
    # Resolve handles → users once, then derive is-agent from the binding (never
    # a hard-coded handle check): a member is an agent iff it carries a binding.
    rows = {
        m.member_handle: await users.get_by_handle(m.member_handle) for m in members
    }
    agent_ids = await bindings.agent_user_ids(
        [u.id for u in rows.values() if u is not None]
    )
    items = []
    for m in members:
        d = TopicMemberOut.model_validate(m).model_dump(mode="json")
        user = rows.get(m.member_handle)
        d["name"] = user.name if user and user.name else m.member_handle
        # Agent members wear an Agent badge — derived from the execution binding.
        d["agent"] = user is not None and user.id in agent_ids
        items.append(d)
    return ok(page(items, total))


@router.post("/{topic_id}/members")
async def add_topic_member(
    topic_id: uuid.UUID,
    body: TopicMemberCreate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    # A verified token pins the acting handle; body.actor is the Phase-0 fallback.
    who = await resolver.resolve(fallback_handle=body.actor, topic_id=topic_id)
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
    who = await resolver.resolve(fallback_handle=body.actor, topic_id=topic_id)
    member = await TopicMemberService(db).update_role(
        topic_id=topic_id, handle=handle, role=body.role, actor=who.handle
    )
    await db.commit()
    return ok(TopicMemberOut.model_validate(member).model_dump(mode="json"))


@router.delete("/{topic_id}/members/{handle}")
async def remove_topic_member(
    topic_id: uuid.UUID,
    handle: str,
    actor: str,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve(fallback_handle=actor, topic_id=topic_id)
    await TopicMemberService(db).remove(
        topic_id=topic_id, handle=handle, actor=who.handle
    )
    await db.commit()
    return ok({"deleted": True})
