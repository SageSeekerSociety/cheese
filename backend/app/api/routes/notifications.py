"""Notification routes — spec §8.5/8.6, evals G2/G3.

Spans two resource prefixes (per-project collection + per-notification actions),
so this router uses an empty prefix and spells out each path.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, ForbiddenError
from app.domain.cx_notification.schemas import (
    FeedbackIn,
    NotificationCreate,
    NotificationOut,
    ResolveIn,
)
from app.domain.cx_notification.services import NotificationService

router = APIRouter(prefix="", tags=["notifications"])

DbSession = Annotated[AsyncSession, Depends(get_db)]

_ANONYMOUS = "anonymous"


def _dump(notification) -> dict:
    return NotificationOut.model_validate(notification).model_dump(mode="json")


async def _recipient(
    resolver: ActorResolver, project_id: uuid.UUID, requested: str | None
) -> str:
    """Whose mailbox this request addresses.

    ``target_handle`` used to be an unauthenticated filter that defaulted to
    "no filter", so omitting it returned every notification in the project —
    other people's included — and ``read-all`` overwrote their ``read_at``.
    A recipient is an identity, so it is resolved at the trust boundary like
    every other actor (fusion-design §4): a verified token names the recipient,
    a token-less caller keeps the Phase-0 handle fallback, and a caller who
    names nobody collapses to ``anonymous`` — which matches broadcasts only,
    never somebody else's mail.

    Reading another handle's mailbox has no authorized caller today (the board
    view that legitimately spans everyone is ``/projects/{id}/overview``, which
    aggregates below the HTTP layer), so an authenticated actor asking for a
    handle that is not its own is refused rather than silently redirected.
    """
    wanted = (requested or "").strip() or None
    actor = await resolver.resolve(fallback_handle=wanted, project_id=project_id)
    if actor.authenticated and wanted is not None and wanted != actor.handle:
        raise ForbiddenError("不能查看别人的通知")
    return actor.handle


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
    handle = await _recipient(resolver, project_id, target_handle)
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
    handle = await _recipient(resolver, project_id, target_handle)
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
    handle = await _recipient(resolver, project_id, target_handle)
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
    handle = await _recipient(resolver, project_id, target_handle)
    if handle == _ANONYMOUS:
        raise AuthenticationRequiredError("需要先登录才能标记已读")
    marked = await NotificationService(db).mark_all_read(
        project_id, target_handle=handle
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
