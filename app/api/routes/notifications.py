from __future__ import annotations

from typing import Annotated
from datetime import datetime, timezone
import base64
import json

from fastapi import APIRouter, Depends, Path, Query, status

from app.auth.checker import get_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError, NotFoundError
from app.core.config import settings
from app.db.session import get_db
from app.domain.notification.models import Notification, NotificationType
from app.domain.notification.repositories import NotificationRepository
from app.domain.notification.services import NotificationQueryService
from app.domain.notification.entity_resolvers import (
    TeamEntityResolver,
    UserEntityResolver,
    ProjectEntityResolver,
)
from app.domain.team.repositories import TeamRepository
from app.domain.team.services import TeamService
from app.domain.user.repositories import UserProfileRepository
from app.domain.user.services import UserService
from app.domain.project.repositories import ProjectRepository
from app.domain.project.services import ProjectService


router = APIRouter(prefix="/notifications", tags=["Notifications"])


async def get_notification_service(
    db=Depends(get_db),
) -> NotificationQueryService:
    repo = NotificationRepository(session=db)
    # Register entity resolvers (team, user, project)
    team_repo = TeamRepository(session=db)
    team_service = TeamService(team_repo)
    team_resolver = TeamEntityResolver(team_service=team_service, legacy_url=settings.legacy_url)

    user_profile_repo = UserProfileRepository(session=db)
    user_service = UserService(user_profile_repo)
    user_resolver = UserEntityResolver(user_service=user_service, legacy_url=settings.legacy_url)

    project_repo = ProjectRepository(session=db)
    project_service = ProjectService(project_repo)
    project_resolver = ProjectEntityResolver(project_service=project_service)

    resolvers = [team_resolver, user_resolver, project_resolver]

    return NotificationQueryService(repo, resolvers=resolvers)


def _notification_to_api_model(notification: Notification) -> dict:
    created_at_ms: int | None = (
        int(notification.created_at.timestamp() * 1000)
        if getattr(notification, "created_at", None) is not None
        else None
    )
    return {
        "id": notification.id,
        "type": notification.type.value if isinstance(notification.type, NotificationType) else str(notification.type),
        "read": notification.read,
        "createdAt": created_at_ms or 0,
        # TODO: entities/contextMetadata to be populated when metadata resolution is ported
        "entities": None,
        "contextMetadata": {},
    }


class BulkUpdateNotificationItem(dict):
    id: int
    read: bool


@router.get("/unread-count", summary="Get Unread Notification Count")
async def get_unread_notifications_count(
    service: NotificationQueryService = Depends(get_notification_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    count = await service.get_unread_notification_count_for_current_user(
        user_id=auth_user.user_id
    )
    return {"code": 200, "message": "Success", "data": {"count": count}}


@router.get(
    "/{notificationId}",
    summary="Get notification by id for current user",
)
async def get_notification_by_id(
    notification_id: Annotated[int, Path(ge=1, alias="notificationId")],
    service: NotificationQueryService = Depends(get_notification_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    notification = await service.get_notification_by_id_for_current_user(
        user_id=auth_user.user_id, notification_id=notification_id
    )
    if notification is None:
        raise NotFoundError("Resource notification not found", data={"type": "notification", "id": notification_id})

    dto = await service.build_notification_dto(notification)

    return {
        "code": 200,
        "message": "Success",
        "data": {"notification": dto.__dict__},
    }


@router.get(
    "",
    summary="List notifications for current user",
)
async def list_notifications(
    page_start: str | None = Query(default=None, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    type_: NotificationType | None = Query(default=None, alias="type"),
    read: bool | None = Query(default=None),
    service: NotificationQueryService = Depends(get_notification_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    # Decode cursor (pageStart) into created_at/id pair, if present.
    cursor_created_at: datetime | None = None
    cursor_id: int | None = None
    if page_start:
        try:
            raw = base64.urlsafe_b64decode(page_start.encode("utf-8")).decode("utf-8")
            data = json.loads(raw)
            ts = data.get("createdAt")
            cid = data.get("id")
            if isinstance(ts, (int, float)) and isinstance(cid, int):
                cursor_created_at = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
                cursor_id = cid
        except Exception:
            cursor_created_at = None
            cursor_id = None

    notifications = await service.get_notifications_for_current_user(
        user_id=auth_user.user_id,
        limit=page_size,
        cursor_created_at=cursor_created_at,
        cursor_id=cursor_id,
        type_=type_,
        read=read,
    )
    # Build DTOs with resolved entities and convert to plain dicts
    items = [(await service.build_notification_dto(n)).__dict__ for n in notifications]

    total = await service.count_notifications_for_current_user(
        user_id=auth_user.user_id,
        type_=type_,
        read=read,
    )

    returned = len(items)

    # Encode next cursor using last item's createdAt/id.
    next_start: str | None = None
    has_more = total > 0 and returned > 0 and total > returned
    if has_more:
        last = notifications[-1]
        last_created_ms = int(last.created_at.timestamp() * 1000)
        cursor_payload = {"createdAt": last_created_ms, "id": last.id}
        encoded = base64.urlsafe_b64encode(
            json.dumps(cursor_payload).encode("utf-8")
        ).decode("utf-8")
        next_start = encoded

    return {
        "code": 200,
        "message": "Success",
        "data": {
            "notifications": items,
            "page": {
                # EncodedCursorPage 兼容结构，pageStart/nextStart 使用简单的 Base64 JSON 游标。
                "pageStart": page_start or "",
                "pageSize": returned,
                "hasMore": has_more,
                "nextStart": next_start,
                "total": total,
            },
        },
    }


@router.patch(
    "",
    summary="Bulk Update Notification Status",
)
async def bulk_update_notifications(
    payload: dict,
    service: NotificationQueryService = Depends(get_notification_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    """Bulk update notification read status for current user.

    Request shape is aligned with NT-API.yml: {updates: [{id, read}, ...]}.
    """
    updates_raw = payload.get("updates") or []
    updates: list[tuple[int, bool]] = []
    for item in updates_raw:
        try:
            nid = int(item["id"])
            read = bool(item["read"])
        except Exception as exc:
            raise BadRequestError(f"Invalid update payload: {exc}")
        updates.append((nid, read))

    updated_ids = await service.bulk_set_read_status(
        user_id=auth_user.user_id,
        updates=updates,
    )

    return {"code": 200, "message": "Success", "data": {"updatedIds": updated_ids}}


@router.put(
    "/status",
    summary="Set Collective Notification Status",
)
async def set_collective_notification_status(
    payload: dict,
    service: NotificationQueryService = Depends(get_notification_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    """Set collective read status for all notifications.

    Currently only supports setting read=true, consistent with Kotlin implementation.
    """
    read = payload.get("read")
    if read is not True:
        raise BadRequestError(
            "This operation only supports marking all notifications as read (read must be true)."
        )

    count = await service.mark_all_as_read_for_current_user(user_id=auth_user.user_id)

    return {"code": 200, "message": "Success", "data": {"count": count}}


@router.patch(
    "/{notificationId}",
    summary="Update Notification Status",
)
async def update_notification_status(
    notification_id: Annotated[int, Path(ge=1, alias="notificationId")],
    payload: dict,
    service: NotificationQueryService = Depends(get_notification_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    read = payload.get("read")
    if read is None:
        raise BadRequestError("'read' field is required")

    # Update then refetch for DTO
    affected = await service.set_read_status(
        user_id=auth_user.user_id,
        notification_id=notification_id,
        desired_read_status=bool(read),
    )
    if affected == 0:
        raise NotFoundError(
            "Resource notification not found", data={"type": "notification", "id": notification_id}
        )

    notification = await service.get_notification_by_id_for_current_user(
        user_id=auth_user.user_id,
        notification_id=notification_id,
    )
    if notification is None:
        raise NotFoundError(
            "Resource notification not found", data={"type": "notification", "id": notification_id}
        )

    dto = await service.build_notification_dto(notification)

    return {
        "code": 200,
        "message": "Success",
        "data": {"notification": dto.__dict__},
    }


@router.delete(
    "/{notificationId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a Notification",
)
async def delete_notification(
    notification_id: Annotated[int, Path(ge=1, alias="notificationId")],
    service: NotificationQueryService = Depends(get_notification_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> None:
    deleted = await service.delete_notification_for_current_user(
        user_id=auth_user.user_id,
        notification_id=notification_id,
    )
    if not deleted:
        raise NotFoundError(
            "Resource notification not found", data={"type": "notification", "id": notification_id}
        )
