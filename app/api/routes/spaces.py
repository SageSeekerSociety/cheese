import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from app.auth.checker import get_auth_user, require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.db.session import get_db
from app.domain.space.analytics_service import SpaceAnalyticsService
from app.domain.space.analytics_view_service import SpaceAnalyticsViewService
from app.domain.space.member_participating_service import SpaceMemberParticipatingService
from app.domain.space.member_publishing_service import SpaceMemberPublishingService
from app.domain.space.models import Space, SpaceAdminRelation, SpaceAdminRole, SpaceCategory
from app.domain.space.repositories import (
    SpaceAdminRelationRepository,
    SpaceCategoryRepository,
    SpaceRepository,
    SpaceUserRankRepository,
)
from app.domain.space.services import SpaceService
from app.domain.space.topics_service import SpaceTopicsService
from app.domain.task.repositories import TaskMembershipRepository, TaskRepository
from app.domain.user.realname_services import UserRealNameService
from app.domain.user.repositories import (
    UserProfileRepository,
    UserRealNameRepository,
    UserRepository,
)

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/spaces", tags=["Spaces"])


def _expect_list(value: list | None, field: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise BadRequestError(f"{field} must be a list")
    return value


async def get_space_service(db=Depends(get_db)) -> SpaceService:
    repo = SpaceRepository(session=db)
    category_repo = SpaceCategoryRepository(session=db)
    admin_repo = SpaceAdminRelationRepository(session=db)
    rank_repo = SpaceUserRankRepository(session=db)
    task_repo = TaskRepository(session=db)
    return SpaceService(repo, category_repo, admin_repo, rank_repo, task_repo)


async def get_space_analytics_service(db=Depends(get_db)) -> SpaceAnalyticsService:
    task_repo = TaskRepository(session=db)
    membership_repo = TaskMembershipRepository(session=db)
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    return SpaceAnalyticsService(
        task_repo=task_repo,
        membership_repo=membership_repo,
        user_repo=user_repo,
        profile_repo=profile_repo,
    )


async def get_space_member_publishing_service(
    db=Depends(get_db),
) -> SpaceMemberPublishingService:
    return SpaceMemberPublishingService(session=db)


async def get_space_member_participating_service(
    db=Depends(get_db),
) -> SpaceMemberParticipatingService:
    return SpaceMemberParticipatingService(session=db)


async def get_space_analytics_view_service(
    db=Depends(get_db),
) -> SpaceAnalyticsViewService:
    return SpaceAnalyticsViewService(session=db)


async def get_space_topics_service(
    db=Depends(get_db),
) -> SpaceTopicsService:
    return SpaceTopicsService(session=db)


async def get_space_user_realname_service(
    db=Depends(get_db),
) -> UserRealNameService:
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    realname_repo = UserRealNameRepository(session=db)
    return UserRealNameService(
        session=db,
        user_repo=user_repo,
        profile_repo=profile_repo,
        realname_repo=realname_repo,
    )


def _space_to_api_model(space: Space) -> dict:
    created_at_ms = int(space.created_at.timestamp() * 1000) if space.created_at else 0
    updated_at_ms = int(space.updated_at.timestamp() * 1000) if space.updated_at else 0
    return {
        "id": space.id,
        "name": space.name,
        "intro": space.intro,
        "description": space.description,
        "avatarId": space.avatar_id,
        "enableRank": space.enable_rank,
        "defaultCategoryId": space.default_category_id,
        "announcements": space.announcements or [],
        "taskTemplates": space.task_templates or [],
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
    }


def _category_to_api_model(cat: SpaceCategory) -> dict:
    created_at_ms = int(cat.created_at.timestamp() * 1000) if cat.created_at else 0
    updated_at_ms = int(cat.updated_at.timestamp() * 1000) if cat.updated_at else 0
    raw_archived_at = getattr(cat, "archived_at", None)
    archived_at_ms = int(raw_archived_at.timestamp() * 1000) if raw_archived_at else None
    return {
        "id": cat.id,
        "spaceId": cat.space_id,
        "name": cat.name,
        "description": cat.description,
        "displayOrder": cat.display_order,
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
        "archivedAt": archived_at_ms,
    }


def _admin_to_api_model(
    rel: SpaceAdminRelation,
    user_info: dict | None = None,
) -> dict:
    created_at_ms = int(rel.created_at.timestamp() * 1000) if rel.created_at else 0
    role_name_map = {SpaceAdminRole.OWNER.value: "OWNER", SpaceAdminRole.ADMIN.value: "ADMIN"}
    result: dict = {
        "userId": rel.user_id,
        "role": role_name_map.get(rel.role, "ADMIN"),
        "createdAt": created_at_ms,
    }
    if user_info is not None:
        result["user"] = user_info
    return result


@router.get(
    "/{spaceId}",
    summary="Query Space",
)
async def get_space(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    queryMyRank: bool = Query(default=False),
    queryCategories: bool = Query(default=False),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    space = await service.get_space(space_id=space_id)
    if space is None:
        raise NotFoundError("Resource space not found", data={"type": "space", "id": space_id})

    categories = None
    if queryCategories:
        cats = await service.list_categories(space_id=space_id, include_archived=False)
        categories = [_category_to_api_model(c) for c in cats]

    my_rank = None
    viewer_id = auth_user.user_id if auth_user.user_id > 0 else None
    if queryMyRank:
        my_rank = await service.get_user_rank(space_id, viewer_id)

    # Include admins with user info
    admin_relations = await service.list_admins(space_id)
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    admins_list = []
    for rel in admin_relations:
        user = await user_repo.get_by_id(rel.user_id)
        profile = await profile_repo.get_profile_by_user_id(rel.user_id) if user else None
        user_info = {
            "id": user.id,
            "username": user.username,
            "nickname": profile.nickname if profile else user.username,
            "avatarId": profile.avatar_id if profile else None,
            "intro": profile.intro if profile else "",
        } if user else {"id": rel.user_id, "username": "unknown"}
        admins_list.append(_admin_to_api_model(rel, user_info))

    space_data = _space_to_api_model(space)
    space_data["admins"] = admins_list

    data: dict = {
        "space": space_data,
        "categories": categories,
    }
    if queryMyRank:
        data["myRank"] = my_rank
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "",
    summary="Enumerate Spaces",
)
async def get_spaces(
    queryMyRank: bool = Query(default=False),
    pageStart: int | None = Query(default=None),
    pageSize: int = Query(default=20, ge=1, le=200),
    service: SpaceService = Depends(get_space_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    offset = pageStart or 0
    spaces = await service.list_spaces(limit=pageSize, offset=offset)
    total = await service.count_spaces()
    viewer_id = auth_user.user_id if auth_user.user_id > 0 else None

    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)

    items: list[dict] = []
    for s in spaces:
        dto = _space_to_api_model(s)
        if queryMyRank:
            dto["myRank"] = await service.get_user_rank(s.id, viewer_id)

        admin_relations = await service.list_admins(s.id)
        admins_list = []
        for rel in admin_relations:
            user = await user_repo.get_by_id(rel.user_id)
            profile = await profile_repo.get_profile_by_user_id(rel.user_id) if user else None
            user_info = {
                "id": user.id,
                "username": user.username,
                "nickname": profile.nickname if profile else user.username,
                "avatarId": profile.avatar_id if profile else None,
                "intro": profile.intro if profile else "",
            } if user else {"id": rel.user_id, "username": "unknown"}
            admins_list.append(_admin_to_api_model(rel, user_info))
        dto["admins"] = admins_list

        items.append(dto)

    returned = len(items)
    has_more = offset + returned < total
    next_start = offset + returned if has_more and returned > 0 else None

    page = {
        "pageStart": offset,
        "pageSize": returned,
        "hasMore": has_more,
        "nextStart": next_start,
        "total": total,
    }
    return {"code": 200, "message": "OK", "data": {"spaces": items, "page": page}}


@router.post(
    "",
    summary="Create Space",
    status_code=status.HTTP_201_CREATED,
)
async def create_space(
    payload: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> dict:
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise BadRequestError("name is required")

    if await service.exists_by_name(name):
        raise ConflictError(f"Space with name '{name}' already exists")

    intro = payload.get("intro") or ""
    description = payload.get("description") or ""
    avatar_id = payload.get("avatarId")
    enable_rank = bool(payload.get("enableRank", False))
    announcements = _expect_list(payload.get("announcements"), "announcements")
    task_templates = _expect_list(payload.get("taskTemplates"), "taskTemplates")
    space = await service.create_space(
        name=name,
        intro=intro,
        description=description,
        avatar_id=avatar_id,
        enable_rank=enable_rank,
        owner_id=auth_user.user_id,
        announcements=announcements,
        task_templates=task_templates,
    )
    return {
        "code": 201,
        "message": "Created",
        "data": {"space": _space_to_api_model(space)},
    }


@router.patch(
    "/{spaceId}",
    summary="Update Space",
)
async def patch_space(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    payload: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> dict:
    announcements = payload.get("announcements")
    task_templates = payload.get("taskTemplates")
    if announcements is not None:
        announcements = _expect_list(announcements, "announcements")
    if task_templates is not None:
        task_templates = _expect_list(task_templates, "taskTemplates")

    space = await service.update_space(
        space_id=space_id,
        actor_user_id=auth_user.user_id,
        name=payload.get("name"),
        intro=payload.get("intro"),
        description=payload.get("description"),
        avatar_id=payload.get("avatarId"),
        enable_rank=payload.get("enableRank"),
        announcements=announcements,
        task_templates=task_templates,
        default_category_id=payload.get("defaultCategoryId"),
    )
    return {"code": 200, "message": "OK", "data": {"space": _space_to_api_model(space)}}


@router.delete(
    "/{spaceId}",
    summary="Delete Space",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_space(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> None:
    await service.delete_space(space_id=space_id, actor_user_id=auth_user.user_id)
    return None


@router.get(
    "/{spaceId}/categories",
    summary="List categories in a space",
)
async def list_space_categories(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    includeArchived: bool = Query(default=False),
    service: SpaceService = Depends(get_space_service),
) -> dict:
    cats = await service.list_categories(space_id=space_id, include_archived=includeArchived)
    items = [_category_to_api_model(c) for c in cats]
    return {"code": 200, "message": "OK", "data": {"categories": items}}


@router.get(
    "/{spaceId}/analytics/tasks",
    summary="Get Space Task Analytics",
)
async def get_space_task_analytics(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    categoryId: int | None = Query(default=None),
    publisherId: int | None = Query(default=None),
    taskApproved: str | None = Query(default=None),
    hasPendingReview: bool | None = Query(default=None),
    hasPendingApproval: bool | None = Query(default=None),
    sortBy: str = Query(default="createdAt"),
    sortOrder: str = Query(default="desc"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsViewService = Depends(get_space_analytics_view_service),
) -> dict:
    """Return per-task analytics table rows for the space."""
    _ = auth_user
    data = await service.get_tasks(
        space_id=space_id,
        from_ts=from_ts,
        to_ts=to_ts,
        category_id=categoryId,
        publisher_id=publisherId,
        task_approved=taskApproved,
        has_pending_review=hasPendingReview,
        has_pending_approval=hasPendingApproval,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/publishers/participation",
    summary="Get Publishers Participation",
    deprecated=True,
    description="Deprecated: use GET /spaces/{spaceId}/analytics/publishers instead.",
)
async def get_publishers_participation(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: SpaceAnalyticsService = Depends(get_space_analytics_service),
) -> dict:
    _ = auth_user
    data = await service.get_publishers_participation(space_id=space_id)
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/participants/export",
    summary="Export Space Participants",
    deprecated=True,
    description="Deprecated: use GET /spaces/{spaceId}/analytics/participants/export instead.",
)
async def export_space_participants(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    format: str = Query(default="csv"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: SpaceAnalyticsService = Depends(get_space_analytics_service),
) -> Response:
    _ = auth_user
    if format.lower() != "csv":
        raise BadRequestError("Only csv format is supported")
    csv_payload = await service.export_participants(space_id=space_id)
    return Response(
        content=csv_payload,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=space-{space_id}-participants.csv"},
    )


# ---------------------------------------------------------------------------
# New Analytics Endpoints (NT-API aligned)
# ---------------------------------------------------------------------------


@router.get(
    "/{spaceId}/analytics/overview",
    summary="Get Space Analytics Overview",
)
async def get_space_analytics_overview(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    categoryId: int | None = Query(default=None),
    publisherId: int | None = Query(default=None),
    taskApproved: str | None = Query(default=None),
    groupBy: str = Query(default="day"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsViewService = Depends(get_space_analytics_view_service),
) -> dict:
    """Return KPI cards, trend data, and distribution summaries for the space."""
    _ = auth_user
    data = await service.get_overview(
        space_id=space_id,
        from_ts=from_ts,
        to_ts=to_ts,
        category_id=categoryId,
        publisher_id=publisherId,
        task_approved=taskApproved,
        group_by=groupBy,
    )
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/analytics/alerts",
    summary="Get Space Analytics Alerts",
)
async def get_space_analytics_alerts(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsViewService = Depends(get_space_analytics_view_service),
) -> dict:
    """Return governance alert cards for the space."""
    _ = auth_user
    data = await service.get_alerts(space_id=space_id)
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/analytics/publishers",
    summary="Get Space Analytics Publishers",
)
async def get_space_analytics_publishers(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    categoryId: int | None = Query(default=None),
    taskApproved: str | None = Query(default=None),
    sortBy: str = Query(default="taskCount"),
    sortOrder: str = Query(default="desc"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsViewService = Depends(get_space_analytics_view_service),
) -> dict:
    """Return publisher comparison table data."""
    _ = auth_user
    data = await service.get_publishers(
        space_id=space_id,
        from_ts=from_ts,
        to_ts=to_ts,
        category_id=categoryId,
        task_approved=taskApproved,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/analytics/participants",
    summary="Get Space Analytics Participants",
)
async def get_space_analytics_participants(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    categoryId: int | None = Query(default=None),
    publisherId: int | None = Query(default=None),
    taskApproved: str | None = Query(default=None),
    participationApproved: str | None = Query(default=None),
    completionStatus: str | None = Query(default=None),
    realName: str = Query(default="all"),
    groupBy: str = Query(default="day"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsViewService = Depends(get_space_analytics_view_service),
) -> dict:
    """Return participant population and completion analytics."""
    _ = auth_user
    data = await service.get_participants(
        space_id=space_id,
        from_ts=from_ts,
        to_ts=to_ts,
        category_id=categoryId,
        publisher_id=publisherId,
        task_approved=taskApproved,
        participation_approved=participationApproved,
        completion_status=completionStatus,
        real_name=realName,
        group_by=groupBy,
    )
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/analytics/participants/export",
    summary="Export Space Analytics Participants",
)
async def export_space_analytics_participants(
    request: Request,
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    categoryId: int | None = Query(default=None),
    publisherId: int | None = Query(default=None),
    taskApproved: str | None = Query(default=None),
    participationApproved: str | None = Query(default=None),
    completionStatus: str | None = Query(default=None),
    realName: str = Query(default="all"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsViewService = Depends(get_space_analytics_view_service),
    realname_service: UserRealNameService = Depends(get_space_user_realname_service),
) -> Response:
    """Export participant analytics as CSV (22 columns, NT-aligned).

    Writes one `UserRealNameAccessLog` row per distinct personal (non-team)
    target user to audit real-name data access, matching NT's
    `auditSpaceParticipantExport` behavior.
    """
    csv_text, memberships = await service.export_participants_csv(
        space_id=space_id,
        from_ts=from_ts,
        to_ts=to_ts,
        category_id=categoryId,
        publisher_id=publisherId,
        task_approved=taskApproved,
        participation_approved=participationApproved,
        completion_status=completionStatus,
        real_name=realName,
    )

    # Audit: per-target user real-name access log (dedup by member_id).
    access_reason = (
        "Export space analytics participants with filters: "
        f"from={from_ts}, to={to_ts}, categoryId={categoryId}, "
        f"publisherId={publisherId}, taskApproved={taskApproved}, "
        f"participationApproved={participationApproved}, "
        f"completionStatus={completionStatus}, realName={realName}"
    )
    ip_address = request.client.host if request.client else ""
    seen_target_ids: set[int] = set()
    for m in memberships:
        if m.is_team:
            continue
        if m.member_id in seen_target_ids:
            continue
        seen_target_ids.add(m.member_id)
        try:
            await realname_service.log_access(
                accessor_id=auth_user.user_id,
                target_id=m.member_id,
                access_reason=access_reason,
                access_type="EXPORT",
                ip_address=ip_address,
                module_type="SPACE",
                module_entity_id=space_id,
            )
        except NotFoundError:
            # Target user may have been soft-deleted; skip audit but continue export.
            _logger.warning(
                "Skip participant export audit: user not found",
                extra={"space_id": space_id, "target_id": m.member_id},
            )

    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=space-{space_id}-participants.csv"
        },
    )


@router.get(
    "/{spaceId}/analytics/tasks/export",
    summary="Export Space Analytics Tasks",
)
async def export_space_analytics_tasks(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    categoryId: int | None = Query(default=None),
    publisherId: int | None = Query(default=None),
    taskApproved: str | None = Query(default=None),
    hasPendingReview: bool | None = Query(default=None),
    hasPendingApproval: bool | None = Query(default=None),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsViewService = Depends(get_space_analytics_view_service),
) -> Response:
    """Export task analytics as CSV (16 columns, NT-aligned)."""
    _ = auth_user
    csv_text = await service.export_tasks_csv(
        space_id=space_id,
        from_ts=from_ts,
        to_ts=to_ts,
        category_id=categoryId,
        publisher_id=publisherId,
        task_approved=taskApproved,
        has_pending_review=hasPendingReview,
        has_pending_approval=hasPendingApproval,
    )
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="space-{space_id}-tasks.csv"'
        },
    )


@router.get(
    "/{spaceId}/analytics/publishers/export",
    summary="Export Space Analytics Publishers",
)
async def export_space_analytics_publishers(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    categoryId: int | None = Query(default=None),
    taskApproved: str | None = Query(default=None),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsViewService = Depends(get_space_analytics_view_service),
) -> Response:
    """Export publisher analytics as CSV (11 columns, NT-aligned)."""
    _ = auth_user
    csv_text = await service.export_publishers_csv(
        space_id=space_id,
        from_ts=from_ts,
        to_ts=to_ts,
        category_id=categoryId,
        task_approved=taskApproved,
    )
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="space-{space_id}-publishers.csv"'
        },
    )


# ---------------------------------------------------------------------------
# Space Member Self-Resources (NT-API aligned)
# ---------------------------------------------------------------------------


@router.get(
    "/{spaceId}/me/publishing",
    summary="Get Space My Publishing Overview",
)
async def get_space_me_publishing(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceMemberPublishingService = Depends(get_space_member_publishing_service),
) -> dict:
    """Return the authenticated user's publishing summary in this space."""
    data = await service.get_my_publishing_overview(
        space_id=space_id,
        user_id=auth_user.user_id,
    )
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/me/publishing/tasks",
    summary="Get Space My Published Tasks",
)
async def get_space_me_published_tasks(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    categoryId: int | None = Query(default=None),
    approved: str | None = Query(default=None),
    hasPendingParticipantApproval: bool | None = Query(default=None),
    hasPendingReview: bool | None = Query(default=None),
    sortBy: str = Query(default="createdAt"),
    sortOrder: str = Query(default="desc"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceMemberPublishingService = Depends(get_space_member_publishing_service),
) -> dict:
    """Return the authenticated user's published tasks in this space."""
    items = await service.get_my_published_tasks(
        space_id=space_id,
        user_id=auth_user.user_id,
        from_ts=from_ts,
        to_ts=to_ts,
        category_id=categoryId,
        approved=approved,
        has_pending_participant_approval=hasPendingParticipantApproval,
        has_pending_review=hasPendingReview,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    return {"code": 200, "message": "OK", "data": {"tasks": items}}


@router.get(
    "/{spaceId}/me/participating",
    summary="Get Space My Participating Overview",
)
async def get_space_me_participating(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceMemberParticipatingService = Depends(
        get_space_member_participating_service
    ),
) -> dict:
    """Return the authenticated user's participation summary in this space."""
    data = await service.get_overview(
        space_id=space_id,
        user_id=auth_user.user_id,
    )
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/me/participations",
    summary="Get Space My Participations",
)
async def get_space_me_participations(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    approved: str | None = Query(default=None),
    completionStatus: str | None = Query(default=None),
    identityType: str | None = Query(default=None),
    sortBy: str = Query(default="joinedAt"),
    sortOrder: str = Query(default="desc"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceMemberParticipatingService = Depends(
        get_space_member_participating_service
    ),
) -> dict:
    """Return the authenticated user's participation list in this space."""
    participations = await service.get_participations(
        space_id=space_id,
        user_id=auth_user.user_id,
        approved=approved,
        completion_status=completionStatus,
        identity_type=identityType,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {"participations": participations},
    }


# ---------------------------------------------------------------------------
# Space Topics (NT-API aligned)
# ---------------------------------------------------------------------------


@router.get(
    "/{spaceId}/topics",
    summary="List or search topics in a space",
)
async def get_space_topics(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    keyword: str | None = Query(default=None),
    sort: str = Query(default="name"),
    limit: int = Query(default=20, ge=1, le=100),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: SpaceTopicsService = Depends(get_space_topics_service),
) -> dict:
    """List or search topics associated with the space.

    NT-aligned (see `SpaceController.getSpaceTopics`):
    - Limit is capped at 50 regardless of the client-requested value.
    - If `keyword` is provided, perform a fuzzy search (sort is ignored).
    - Otherwise, return the hottest topics (most non-deleted tasks in the space).
    """
    safe_limit = min(limit, 50)

    if keyword and keyword.strip():
        topics = await service.search_topics(space_id, keyword, safe_limit)
    else:
        topics = await service.get_hot_topics(space_id, safe_limit)

    return {"code": 200, "message": "OK", "data": {"topics": topics}}


@router.post(
    "/{spaceId}/categories",
    summary="Create Space Category",
    status_code=status.HTTP_201_CREATED,
)
async def create_space_category(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    payload: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> dict:
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise BadRequestError("Category name is required")
    description = payload.get("description")
    try:
        display_order_raw = payload.get("displayOrder", 0)
        display_order = int(display_order_raw)
    except (TypeError, ValueError) as exc:
        raise BadRequestError("displayOrder must be integer") from exc
    category = await service.create_category(
        space_id=space_id,
        name=name,
        description=description,
        display_order=display_order,
        actor_user_id=auth_user.user_id,
    )
    return {
        "code": 201,
        "message": "Created",
        "data": {"category": _category_to_api_model(category)},
    }


@router.patch(
    "/{spaceId}/categories/{categoryId}",
    summary="Update Space Category",
)
async def patch_space_category(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    category_id: Annotated[int, Path(ge=1, alias="categoryId")],
    payload: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> dict:
    display_order = None
    if "displayOrder" in payload:
        try:
            display_order = int(payload.get("displayOrder"))
        except (TypeError, ValueError) as exc:
            raise BadRequestError("displayOrder must be integer") from exc

    # Support both "archived" (boolean) and "archivedAt" (timestamp)
    archived = payload.get("archived")
    if archived is None and "archivedAt" in payload:
        archived_at_raw = payload.get("archivedAt")
        archived = archived_at_raw is not None and archived_at_raw > 0

    category = await service.update_category(
        space_id=space_id,
        category_id=category_id,
        actor_user_id=auth_user.user_id,
        name=payload.get("name"),
        description=payload.get("description"),
        display_order=display_order,
        archived=archived,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {"category": _category_to_api_model(category)},
    }


@router.get(
    "/{spaceId}/categories/{categoryId}",
    summary="Get Space Category",
)
async def get_space_category(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    category_id: Annotated[int, Path(ge=1, alias="categoryId")],
    service: SpaceService = Depends(get_space_service),
) -> dict:
    category = await service.get_category_detail(space_id=space_id, category_id=category_id)
    return {
        "code": 200,
        "message": "OK",
        "data": {"category": _category_to_api_model(category)},
    }


@router.delete(
    "/{spaceId}/categories/{categoryId}",
    summary="Delete Space Category",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_space_category(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    category_id: Annotated[int, Path(ge=1, alias="categoryId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> None:
    await service.delete_category(
        space_id=space_id, category_id=category_id, actor_user_id=auth_user.user_id
    )
    return None


@router.post(
    "/{spaceId}/categories/{categoryId}/archive",
    summary="Archive Space Category",
)
async def archive_space_category(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    category_id: Annotated[int, Path(ge=1, alias="categoryId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> dict:
    category = await service.set_category_archived(
        space_id=space_id,
        category_id=category_id,
        archived=True,
        actor_user_id=auth_user.user_id,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {"category": _category_to_api_model(category)},
    }


@router.delete(
    "/{spaceId}/categories/{categoryId}/archive",
    summary="Unarchive Space Category",
)
async def unarchive_space_category(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    category_id: Annotated[int, Path(ge=1, alias="categoryId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> dict:
    category = await service.set_category_archived(
        space_id=space_id,
        category_id=category_id,
        archived=False,
        actor_user_id=auth_user.user_id,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {"category": _category_to_api_model(category)},
    }


@router.get(
    "/{spaceId}/managers",
    summary="List Space Managers",
)
async def list_space_admins(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    service: SpaceService = Depends(get_space_service),
) -> dict:
    admins = await service.list_admins(space_id)
    return {
        "code": 200,
        "message": "OK",
        "data": {"managers": [_admin_to_api_model(rel) for rel in admins]},
    }


@router.post(
    "/{spaceId}/managers",
    summary="Add Space Manager",
    status_code=status.HTTP_201_CREATED,
)
async def add_space_admin(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    payload: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> dict:
    user_id = payload.get("userId")
    if not isinstance(user_id, int) or user_id <= 0:
        raise BadRequestError("userId must be positive integer")
    role_value = (payload.get("role") or "ADMIN").upper()
    role_mapping = {"OWNER": SpaceAdminRole.OWNER, "ADMIN": SpaceAdminRole.ADMIN}
    role = role_mapping.get(role_value)
    if role is None:
        raise BadRequestError(f"Invalid role: {role_value}")
    await service.add_admin(
        space_id=space_id,
        target_user_id=user_id,
        role=role,
        actor_user_id=auth_user.user_id,
    )
    return {"code": 201, "message": "Created"}


@router.delete(
    "/{spaceId}/managers/{userId}",
    summary="Remove Space Manager",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_space_admin(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> Response:
    await service.remove_admin(
        space_id=space_id,
        target_user_id=user_id,
        actor_user_id=auth_user.user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/{spaceId}/managers/{userId}",
    summary="Update Space Manager Role",
)
async def patch_space_manager(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    payload: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> dict:
    role_str = payload.get("role")
    if not isinstance(role_str, str):
        raise BadRequestError("role is required")
    role_map = {"OWNER": SpaceAdminRole.OWNER, "ADMIN": SpaceAdminRole.ADMIN}
    new_role = role_map.get(role_str.upper())
    if new_role is None:
        raise BadRequestError(f"Invalid role: {role_str}. Must be OWNER or ADMIN")
    await service.update_admin_role(
        space_id=space_id,
        target_user_id=user_id,
        new_role=new_role,
        actor_user_id=auth_user.user_id,
    )
    return {"code": 200, "message": "OK"}
