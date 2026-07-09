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
    from app.domain.user.repositories import UserRepository

    members, total = await TopicMemberService(db).list_for_topic(topic_id)
    # Attach display names so the UI can label 头像 without a second round-trip.
    users = UserRepository(db)
    items = []
    for m in members:
        d = TopicMemberOut.model_validate(m).model_dump(mode="json")
        user = await users.get_by_handle(m.member_handle)
        d["name"] = user.name if user and user.name else m.member_handle
        # Agent members (芝士) wear an Agent badge in the UI (like the @ menu).
        d["agent"] = m.member_handle == "cheese"
        items.append(d)
    return ok(page(items, total))


@router.post("/{topic_id}/members")
async def add_topic_member(
    topic_id: uuid.UUID, body: TopicMemberCreate, db: DbSession
) -> dict:
    member = await TopicMemberService(db).add(
        topic_id=topic_id, handle=body.handle, role=body.role, actor=body.actor
    )
    await db.commit()
    return ok(TopicMemberOut.model_validate(member).model_dump(mode="json"))


@router.put("/{topic_id}/members/{handle}")
async def update_topic_member_role(
    topic_id: uuid.UUID,
    handle: str,
    body: TopicMemberRoleUpdate,
    db: DbSession,
) -> dict:
    member = await TopicMemberService(db).update_role(
        topic_id=topic_id, handle=handle, role=body.role, actor=body.actor
    )
    await db.commit()
    return ok(TopicMemberOut.model_validate(member).model_dump(mode="json"))


@router.delete("/{topic_id}/members/{handle}")
async def remove_topic_member(
    topic_id: uuid.UUID, handle: str, actor: str, db: DbSession
) -> dict:
    await TopicMemberService(db).remove(
        topic_id=topic_id, handle=handle, actor=actor
    )
    await db.commit()
    return ok({"deleted": True})
