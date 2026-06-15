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
from app.domain.notification.schemas import (
    FeedbackIn,
    NotificationCreate,
    NotificationOut,
)
from app.domain.notification.services import NotificationService

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
