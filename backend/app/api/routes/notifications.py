"""Notification routes — spec §8.5/8.6, evals G2/G3.

Spans two resource prefixes (per-project collection + per-notification actions),
so this router uses an empty prefix and spells out each path.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok, page
from app.core.db import get_db
from app.domain.cx_notification.schemas import (
    FeedbackIn,
    NotificationCreate,
    NotificationOut,
    ResolveIn,
)
from app.domain.cx_notification.services import NotificationService

router = APIRouter(prefix="", tags=["notifications"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _dump(notification) -> dict:
    return NotificationOut.model_validate(notification).model_dump(mode="json")


@router.post("/api/projects/{project_id}/notifications")
async def create_notification(
    project_id: uuid.UUID, body: NotificationCreate, db: DbSession
) -> dict:
    notification = await NotificationService(db).create(
        project_id=project_id,
        level=body.level,
        kind=body.kind,
        title=body.title,
        body=body.body,
        target_handle=body.target_handle,
        topic_id=body.topic_id,
        payload=body.payload,
    )
    return ok(_dump(notification))


@router.get("/api/projects/{project_id}/notifications")
async def list_notifications(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    target_handle: str | None = None,
    unread_only: bool = False,
) -> dict:
    """This caller's notifications: the ones addressed to them plus broadcasts."""
    handle = await resolver.recipient(project_id=project_id, requested=target_handle)
    items, total = await NotificationService(db).list_for_project(
        project_id, target_handle=handle, unread_only=unread_only
    )
    return ok(page([_dump(n) for n in items], total))


@router.get("/api/projects/{project_id}/inbox")
async def project_inbox(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    target_handle: str | None = None,
) -> dict:
    handle = await resolver.recipient(project_id=project_id, requested=target_handle)
    items, total = await NotificationService(db).inbox(project_id, target_handle=handle)
    return ok(page([_dump(n) for n in items], total))


@router.get("/api/projects/{project_id}/notifications/unread-count")
async def notifications_unread_count(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    target_handle: str | None = None,
) -> dict:
    """Badge count for the bell: unread, non-silent, visible to this user.
    Server-side so the client never has to fetch the full list just to count."""
    handle = await resolver.recipient(project_id=project_id, requested=target_handle)
    count = await NotificationService(db).unread_count(project_id, target_handle=handle)
    return ok({"unread": count})


@router.post("/api/projects/{project_id}/notifications/read-all")
async def mark_all_notifications_read(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    target_handle: str | None = None,
) -> dict:
    """全部标记已读 (Feishu-style) — for the calling user, nobody else.

    Unlike the reads, an unidentified caller is refused instead of being served
    the broadcast-only slice: this writes ``read_at``, and a mailbox nobody can
    be named the owner of is not one anybody may clear.
    """
    actor = await resolver.require_recipient(
        project_id=project_id, target_handle=(target_handle or "").strip() or None
    )
    marked = await NotificationService(db).mark_all_read(
        project_id, target_handle=actor.handle
    )
    return ok({"marked": marked})


@router.post("/api/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Mark one notification read — only the person it was sent to may.

    ``read_at`` is not a per-viewer flag but a column on the row itself, so this
    is a write on somebody's mailbox even though it reads like a UI detail: a
    caller who could reach any id could clear another person's badge one item at
    a time, and the response hands back the notification's full body.
    """
    service = NotificationService(db)
    notification = await service.get_or_404(notification_id)
    await resolver.require_recipient(
        project_id=notification.project_id, target_handle=notification.target_handle
    )
    return ok(_dump(await service.mark_read(notification_id)))


@router.post("/api/notifications/{notification_id}/feedback")
async def set_notification_feedback(
    notification_id: uuid.UUID,
    body: FeedbackIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """👍/👎 on a notification — the recipient's own judgement of it."""
    service = NotificationService(db)
    notification = await service.get_or_404(notification_id)
    await resolver.require_recipient(
        project_id=notification.project_id, target_handle=notification.target_handle
    )
    return ok(_dump(await service.set_feedback(notification_id, body.feedback)))


@router.post("/api/notifications/{notification_id}/resolve")
async def resolve_notification(
    notification_id: uuid.UUID,
    body: ResolveIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """拍板 a decision request (spec G2).

    Who decided is the actor, never ``body.decided_by`` — the choice is written
    into the topic as a 【决策】 block that 芝士 acts on next turn, so a
    body-supplied name let anyone put words in a teammate's mouth. Only the
    person the request was addressed to may answer it.
    """
    service = NotificationService(db)
    notification = await service.get_or_404(notification_id)
    actor = await resolver.require_recipient(
        project_id=notification.project_id, target_handle=notification.target_handle
    )
    resolved = await service.resolve(
        notification_id, chosen=body.chosen, decided_by=actor.handle
    )
    return ok(_dump(resolved))
