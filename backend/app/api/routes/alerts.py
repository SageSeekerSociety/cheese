"""Alert routes — spec §8.5/8.6, evals G2/G3.

Spans two resource prefixes (per-project collection + per-alert actions),
so this router uses an empty prefix and spells out each path.

Every endpoint that reads or writes a person's mailbox resolves the recipient
through ``ActorResolver.resolve_recipient`` — the rule lives at the trust
boundary (app.api.auth), not here, so the next per-recipient endpoint cannot
quietly skip it. ``target_handle`` is only ever an assertion checked against
the verified credential, never an identity by itself.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.response import ok, page
from app.core.db import get_db
from app.domain.alert.models import Alert
from app.domain.alert.schemas import (
    AlertCreate,
    AlertOut,
    FeedbackIn,
    ResolveIn,
)
from app.domain.alert.services import AlertService

router = APIRouter(prefix="", tags=["alerts"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _dump(alert: Alert) -> dict:
    return AlertOut.model_validate(alert).model_dump(mode="json")


async def _acting_recipient(resolver: ActorResolver, alert: Alert) -> str:
    """The verified caller allowed to act on this alert: its addressee,
    or — for a broadcast (no target) — any authenticated caller. Read/feedback/
    resolve write per-alert state, so the anonymous slice never applies.
    """
    return await resolver.resolve_recipient(
        requested=alert.target_handle,
        project_id=alert.project_id,
        allow_anonymous=False,
    )


@router.post("/api/projects/{project_id}/alerts")
async def create_notification(
    project_id: uuid.UUID,
    body: AlertCreate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Post into the project's alert stream — agents (scoped token),
    the dev override, or a signed-in human; never an anonymous drive-by. The
    route checks the credential itself rather than leaning on the middleware
    gate alone (see ``require_verified_caller``)."""
    await resolver.require_verified_caller(project_id=project_id)
    alert = await AlertService(db).create(
        project_id=project_id,
        level=body.level,
        kind=body.kind,
        title=body.title,
        body=body.body,
        target_handle=body.target_handle,
        topic_id=body.topic_id,
        payload=body.payload,
    )
    return ok(_dump(alert))


@router.get("/api/projects/{project_id}/alerts")
async def list_notifications(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    target_handle: str | None = None,
    unread_only: bool = False,
) -> dict:
    """This caller's alerts: the ones addressed to them plus broadcasts."""
    handle = await resolver.resolve_recipient(
        requested=target_handle, project_id=project_id
    )
    items, total = await AlertService(db).list_for_project(
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
    handle = await resolver.resolve_recipient(
        requested=target_handle, project_id=project_id
    )
    items, total = await AlertService(db).inbox(project_id, target_handle=handle)
    return ok(page([_dump(n) for n in items], total))


@router.get("/api/projects/{project_id}/alerts/unread-count")
async def notifications_unread_count(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    target_handle: str | None = None,
) -> dict:
    """Badge count for the bell: unread, non-silent, visible to this user.
    Server-side so the client never has to fetch the full list just to count."""
    handle = await resolver.resolve_recipient(
        requested=target_handle, project_id=project_id
    )
    count = await AlertService(db).unread_count(project_id, target_handle=handle)
    return ok({"unread": count})


@router.post("/api/projects/{project_id}/alerts/read-all")
async def mark_all_notifications_read(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    target_handle: str | None = None,
) -> dict:
    """全部标记已读 (Feishu-style) — for the calling user, nobody else.

    Unlike the reads, there is no anonymous slice here: this writes ``read_at``,
    so the caller must hold a verified identity — naming a handle without one is
    refused, not honored.
    """
    handle = await resolver.resolve_recipient(
        requested=target_handle, project_id=project_id, allow_anonymous=False
    )
    marked = await AlertService(db).mark_all_read(project_id, target_handle=handle)
    return ok({"marked": marked})


@router.post("/api/alerts/{alert_id}/read")
async def mark_notification_read(
    alert_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    service = AlertService(db)
    alert = await service.get_or_404(alert_id)
    await _acting_recipient(resolver, alert)
    return ok(_dump(await service.mark_read(alert_id)))


@router.post("/api/alerts/{alert_id}/feedback")
async def set_notification_feedback(
    alert_id: uuid.UUID,
    body: FeedbackIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    service = AlertService(db)
    alert = await service.get_or_404(alert_id)
    await _acting_recipient(resolver, alert)
    return ok(_dump(await service.set_feedback(alert_id, body.feedback)))


@router.post("/api/alerts/{alert_id}/resolve")
async def resolve_notification(
    alert_id: uuid.UUID,
    body: ResolveIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """拍板 a decision request (spec G2). The decision is attributed to the
    verified caller — a body-supplied name is never trusted."""
    service = AlertService(db)
    alert = await service.get_or_404(alert_id)
    handle = await _acting_recipient(resolver, alert)
    resolved = await service.resolve(alert_id, chosen=body.chosen, decided_by=handle)
    return ok(_dump(resolved))
