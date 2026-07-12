"""知是 flat ``/notifications`` resource — the per-user inbox.

The fusion merge brought over the 知是 notification cluster (service, repo, DTO,
entity resolvers) but dropped its HTTP controller, leaving the service orphaned:
team/membership flows WRITE notifications (``TEAM_JOIN_REQUEST`` etc.) that no
route could READ back. The merged frontend still calls this flat resource from
the top-nav bell (``frontend/src/network/api/notifications``), so those calls
404'd. This router re-wires the existing ``NotificationQueryService`` to the routes,
matching the reference Kotlin ``NotificationController`` contract.

Distinct from the cheesex per-project notifications in ``notifications.py``
(uuid, ``/api/projects/{id}/notifications``); this one is int-keyed and lives at
the root, addressed to the authenticated receiver.
"""

import base64
import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.config import settings
from app.core.errors import BadRequestError, NotFoundError
from app.db.session import get_db
from app.domain.notification.dto import NotificationDTO
from app.domain.notification.entity_resolvers import (
    ProjectEntityResolver,
    TeamEntityResolver,
    UserEntityResolver,
)
from app.domain.notification.models import NotificationType
from app.domain.notification.repositories import NotificationRepository
from app.domain.notification.services import NotificationQueryService
from app.domain.project.services import ProjectService
from app.domain.team.repositories import TeamRepository
from app.domain.team.services import TeamService
from app.domain.user.repositories import UserProfileRepository
from app.domain.user.services import UserService

router = APIRouter(prefix="", tags=["notifications"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
AuthUser = Annotated[AuthUserInfo, Depends(require_auth_user)]


# --- request bodies ----------------------------------------------------------


class NotificationUpdate(BaseModel):
    id: int
    read: bool


class BulkUpdateRequest(BaseModel):
    updates: list[NotificationUpdate]


class ReadStatusRequest(BaseModel):
    read: bool


# --- cursor codec ------------------------------------------------------------
#
# The frontend treats ``pageStart``/``nextStart`` as opaque strings
# (``EncodedCursorPage = Page<string>``) and round-trips them back to this same
# Python backend, so the encoding only has to be self-consistent — it does not
# have to byte-match the retired Kotlin encoder. We encode the full ISO
# timestamp (not truncated ms) so the compound ``(created_at, id)`` cursor keeps
# microsecond precision and the strict ``<`` comparison never skips or repeats a
# boundary row.


def _encode_cursor(created_at: datetime, notification_id: int) -> str:
    raw = json.dumps(
        {"createdAt": created_at.isoformat(), "id": notification_id},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(encoded: str) -> tuple[datetime, int] | None:
    """Return ``(created_at, id)`` or ``None`` for a missing/malformed cursor.

    A bad cursor is treated as "start from the beginning" (like the reference
    backend, which logs and ignores undecodable cursors) rather than a 400."""
    if not encoded:
        return None
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return datetime.fromisoformat(payload["createdAt"]), int(payload["id"])
    except (ValueError, KeyError, TypeError):
        return None


def _read_service(db: AsyncSession) -> NotificationQueryService:
    """Assemble the query service with the entity resolvers so notification DTOs
    carry resolved team/user/project display info (name, avatar)."""
    avatar_url = settings.avatar_base_url
    resolvers = [
        TeamEntityResolver(TeamService(TeamRepository(db)), avatar_url),
        UserEntityResolver(UserService(UserProfileRepository(db)), avatar_url),
        ProjectEntityResolver(ProjectService(db)),
    ]
    return NotificationQueryService(NotificationRepository(db), resolvers=resolvers)


def _parse_type(type_: str | None) -> NotificationType | None:
    if type_ is None:
        return None
    try:
        return NotificationType(type_)
    except ValueError:
        raise BadRequestError(f"Unknown notification type: {type_}") from None


async def _dump(service: NotificationQueryService, notification) -> dict:
    dto: NotificationDTO = await service.build_notification_dto(notification)
    return _asdict(dto)


def _asdict(dto: NotificationDTO) -> dict:
    entities = {
        key: (None if value is None else vars(value))
        for key, value in dto.entities.items()
    }
    return {
        "id": dto.id,
        "type": dto.type,
        "read": dto.read,
        "createdAt": dto.createdAt,
        "entities": entities,
        "contextMetadata": dto.contextMetadata,
    }


# --- routes ------------------------------------------------------------------
# Literal paths are declared before ``/notifications/{id}`` so the int converter
# never swallows "unread-count"/"status".


@router.get("/notifications/unread-count")
async def get_unread_count(user: AuthUser, db: DbSession) -> dict:
    service = _read_service(db)
    count = await service.get_unread_notification_count_for_current_user(user.user_id)
    return ok({"count": count})


@router.get("/notifications")
async def list_notifications(
    user: AuthUser,
    db: DbSession,
    pageSize: Annotated[int, Query(ge=1, le=100)] = 20,
    pageStart: str | None = None,
    type: str | None = None,
    read: bool | None = None,
) -> dict:
    service = _read_service(db)
    notification_type = _parse_type(type)
    cursor = _decode_cursor(pageStart) if pageStart else None
    cursor_created_at, cursor_id = cursor if cursor else (None, None)

    # Fetch one extra row to detect whether a further page exists.
    rows = await service.get_notifications_for_current_user(
        user.user_id,
        limit=pageSize + 1,
        cursor_created_at=cursor_created_at,
        cursor_id=cursor_id,
        type_=notification_type,
        read=read,
    )
    has_more = len(rows) > pageSize
    content = list(rows[:pageSize])
    total = await service.count_notifications_for_current_user(
        user.user_id, type_=notification_type, read=read
    )

    notifications = [await _dump(service, n) for n in content]
    page = {
        "pageStart": _encode_cursor(content[0].created_at, content[0].id)
        if content
        else "",
        "pageSize": len(content),
        "hasMore": has_more,
        "nextStart": _encode_cursor(content[-1].created_at, content[-1].id)
        if has_more
        else None,
        "total": total,
    }
    return ok({"notifications": notifications, "page": page})


@router.patch("/notifications")
async def bulk_update_notifications(
    body: BulkUpdateRequest, user: AuthUser, db: DbSession
) -> dict:
    service = _read_service(db)
    updates = [(u.id, u.read) for u in body.updates]
    updated_ids = await service.bulk_set_read_status(user.user_id, updates)
    return ok({"updatedIds": updated_ids})


@router.put("/notifications/status")
async def set_collective_status(
    body: ReadStatusRequest, user: AuthUser, db: DbSession
) -> dict:
    # Mirrors the reference contract: only bulk mark-as-read is supported.
    if not body.read:
        raise BadRequestError(
            "This operation only supports marking all notifications as read "
            "(read must be true)."
        )
    count = await _read_service(db).mark_all_as_read_for_current_user(user.user_id)
    return ok({"count": count})


@router.get("/notifications/{notification_id}")
async def get_notification(
    notification_id: int, user: AuthUser, db: DbSession
) -> dict:
    service = _read_service(db)
    notification = await service.get_notification_by_id_for_current_user(
        user.user_id, notification_id
    )
    if notification is None:
        raise NotFoundError(f"Notification {notification_id} not found")
    return ok({"notification": await _dump(service, notification)})


@router.patch("/notifications/{notification_id}")
async def update_notification_status(
    notification_id: int, body: ReadStatusRequest, user: AuthUser, db: DbSession
) -> dict:
    service = _read_service(db)
    affected = await service.set_read_status(user.user_id, notification_id, body.read)
    if affected == 0:
        raise NotFoundError(f"Notification {notification_id} not found")
    notification = await service.get_notification_by_id_for_current_user(
        user.user_id, notification_id
    )
    if notification is None:
        raise NotFoundError(f"Notification {notification_id} not found")
    return ok({"notification": await _dump(service, notification)})


@router.delete("/notifications/{notification_id}")
async def delete_notification(
    notification_id: int, user: AuthUser, db: DbSession
) -> Response:
    service = _read_service(db)
    deleted = await service.delete_notification_for_current_user(
        user.user_id, notification_id
    )
    if not deleted:
        raise NotFoundError(f"Notification {notification_id} not found")
    return Response(status_code=204)
