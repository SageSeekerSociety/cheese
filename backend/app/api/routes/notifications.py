"""Notification routes — spec §8.5/8.6, evals G2/G3.

Spans two resource prefixes (per-project collection + per-notification actions),
so this router uses an empty prefix and spells out each path.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

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
    target_handle: str | None = None,
    unread_only: bool = False,
) -> dict:
    items, total = await NotificationService(db).list_for_project(
        project_id, target_handle=target_handle, unread_only=unread_only
    )
    return ok(page([_dump(n) for n in items], total))


@router.get("/api/projects/{project_id}/inbox")
async def project_inbox(
    project_id: uuid.UUID,
    db: DbSession,
    target_handle: str | None = None,
) -> dict:
    items, total = await NotificationService(db).inbox(
        project_id, target_handle=target_handle
    )
    return ok(page([_dump(n) for n in items], total))


@router.get("/api/projects/{project_id}/notifications/unread-count")
async def notifications_unread_count(
    project_id: uuid.UUID,
    db: DbSession,
    target_handle: str | None = None,
) -> dict:
    """Badge count for the bell: unread, non-silent, visible to this user.
    Server-side so the client never has to fetch the full list just to count."""
    count = await NotificationService(db).unread_count(
        project_id, target_handle=target_handle
    )
    return ok({"unread": count})


@router.post("/api/projects/{project_id}/notifications/read-all")
async def mark_all_notifications_read(
    project_id: uuid.UUID,
    db: DbSession,
    target_handle: str | None = None,
) -> dict:
    """全部标记已读 (Feishu-style)."""
    marked = await NotificationService(db).mark_all_read(
        project_id, target_handle=target_handle
    )
    return ok({"marked": marked})


@router.post("/api/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: uuid.UUID, db: DbSession) -> dict:
    notification = await NotificationService(db).mark_read(notification_id)
    return ok(_dump(notification))


@router.post("/api/notifications/{notification_id}/feedback")
async def set_notification_feedback(
    notification_id: uuid.UUID, body: FeedbackIn, db: DbSession
) -> dict:
    notification = await NotificationService(db).set_feedback(
        notification_id, body.feedback
    )
    return ok(_dump(notification))


@router.post("/api/notifications/{notification_id}/resolve")
async def resolve_notification(
    notification_id: uuid.UUID, body: ResolveIn, db: DbSession
) -> dict:
    """拍板 a decision request (spec G2)."""
    notification = await NotificationService(db).resolve(
        notification_id, chosen=body.chosen, decided_by=body.decided_by
    )
    return ok(_dump(notification))
