import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.db.session import get_db
from app.domain.space.analytics_service import SpaceAnalyticsService
from app.domain.space.analytics_view_service import SpaceAnalyticsViewService
from app.domain.space.member_participating_service import (
    SpaceMemberParticipatingService,
)
from app.domain.space.member_publishing_service import SpaceMemberPublishingService
from app.domain.space.models import (
    Space,
    SpaceAdminRelation,
    SpaceAdminRole,
    SpaceCategory,
    SpaceDomainGroup,
)
from app.domain.space.repositories import (
    SpaceAdminRelationRepository,
    SpaceCategoryRepository,
    SpaceClassificationTopicsRepository,
    SpaceDomainGroupDomainRepository,
    SpaceDomainGroupRepository,
    SpaceRepository,
    SpaceUserRankRepository,
)
from app.domain.space.services import SpaceService
from app.domain.space.tags_service import SpaceTagsService
from app.domain.task.repositories import TaskMembershipRepository, TaskRepository
from app.domain.user.realname_services import UserRealNameService
from app.domain.user.repositories import (
    UserProfileRepository,
    UserRealNameRepository,
    UserRepository,
)

_logger = logging.getLogger(__name__)


# ── Request Models ────────────────────────────────────────────────────────────


class CreateSpaceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1)
    intro: str = ""
    description: str = ""
    avatar_id: int | None = Field(default=None, alias="avatarId")
    enable_rank: bool = Field(default=False, alias="enableRank")
    announcements: list | str | None = None
    task_templates: list | str | None = Field(default=None, alias="taskTemplates")
    classification_topics: list[int] | None = Field(
        default=None, alias="classificationTopics"
    )
    visible_task_limit: int | None = Field(default=None, alias="visibleTaskLimit")

    @field_validator("visible_task_limit", mode="before")
    @classmethod
    def _validate_visible_task_limit(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("visibleTaskLimit must be null or a non-negative integer")
        return value


class PatchSpaceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    intro: str | None = None
    description: str | None = None
    avatar_id: int | None = Field(default=None, alias="avatarId")
    enable_rank: bool | None = Field(default=None, alias="enableRank")
    announcements: list | str | None = None
    task_templates: list | str | None = Field(default=None, alias="taskTemplates")
    classification_topics: list[int] | None = Field(
        default=None, alias="classificationTopics"
    )
    default_category_id: int | None = Field(default=None, alias="defaultCategoryId")
    visible_task_limit: int | None = Field(default=None, alias="visibleTaskLimit")

    @field_validator("visible_task_limit", mode="before")
    @classmethod
    def _validate_visible_task_limit(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("visibleTaskLimit must be null or a non-negative integer")
        return value


class CreateSpaceCategoryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1)
    description: str | None = None
    display_order: int = Field(default=0, alias="displayOrder")


class PatchSpaceCategoryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    description: str | None = None
    display_order: int | None = Field(default=None, alias="displayOrder")
    archived: bool | None = None
    archived_at: int | None = Field(default=None, alias="archivedAt")


class CreateSpaceDomainGroupRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1)
    description: str | None = None
    domains: list[str] = Field(default_factory=list)


class PatchSpaceDomainGroupRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    description: str | None = None
    domains: list[str] | None = None


class AddSpaceManagerRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: int = Field(..., alias="userId", gt=0)
    role: str = "ADMIN"


class PatchSpaceManagerRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role: str = Field(..., min_length=1)


router = APIRouter(prefix="/spaces", tags=["Spaces"])


def _expect_list(value: list | str | None, field: str) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise BadRequestError(f"{field} must be a valid JSON array") from exc
        if isinstance(parsed, list):
            return parsed
        raise BadRequestError(f"{field} must be a JSON array")
    raise BadRequestError(f"{field} must be a list or a JSON array string")


async def get_space_service(db=Depends(get_db)) -> SpaceService:
    repo = SpaceRepository(session=db)
    category_repo = SpaceCategoryRepository(session=db)
    admin_repo = SpaceAdminRelationRepository(session=db)
    rank_repo = SpaceUserRankRepository(session=db)
    task_repo = TaskRepository(session=db)
    classification_topics_repo = SpaceClassificationTopicsRepository(session=db)
    domain_group_repo = SpaceDomainGroupRepository(session=db)
    domain_group_domain_repo = SpaceDomainGroupDomainRepository(session=db)
    return SpaceService(
        repo,
        category_repo,
        admin_repo,
        rank_repo,
        task_repo,
        classification_topics_repo=classification_topics_repo,
        domain_group_repo=domain_group_repo,
        domain_group_domain_repo=domain_group_domain_repo,
    )


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
) -> SpaceTagsService:
    return SpaceTagsService(session=db)


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
        "visibleTaskLimit": space.visible_task_limit,
        "defaultCategoryId": space.default_category_id,
        "announcements": json.dumps(space.announcements or []),
        "taskTemplates": json.dumps(space.task_templates or []),
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
    }


def _category_to_api_model(cat: SpaceCategory) -> dict:
    created_at_ms = int(cat.created_at.timestamp() * 1000) if cat.created_at else 0
    updated_at_ms = int(cat.updated_at.timestamp() * 1000) if cat.updated_at else 0
    raw_archived_at = getattr(cat, "archived_at", None)
    archived_at_ms = (
        int(raw_archived_at.timestamp() * 1000) if raw_archived_at else None
    )
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


def _domain_group_to_api_model(group: SpaceDomainGroup, domains: list[str]) -> dict:
    created_at_ms = int(group.created_at.timestamp() * 1000) if group.created_at else 0
    updated_at_ms = int(group.updated_at.timestamp() * 1000) if group.updated_at else 0
    return {
        "id": group.id,
        "spaceId": group.space_id,
        "name": group.name,
        "description": group.description,
        "domains": domains,
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
    }


def _admin_to_api_model(
    rel: SpaceAdminRelation,
    user_info: dict | None = None,
) -> dict:
    created_at_ms = int(rel.created_at.timestamp() * 1000) if rel.created_at else 0
    role_name_map = {
        SpaceAdminRole.OWNER.value: "OWNER",
        SpaceAdminRole.ADMIN.value: "ADMIN",
    }
    result: dict = {
        "userId": rel.user_id,
        "role": role_name_map.get(rel.role, "ADMIN"),
        "createdAt": created_at_ms,
    }
    if user_info is not None:
        result["user"] = user_info
    return result


async def _build_admins_payload(
    space_id: int,
    *,
    service: SpaceService,
    user_repo: UserRepository,
    profile_repo: UserProfileRepository,
) -> list[dict]:
    """Hydrate admin relations with full user objects.

    The frontend Space type requires `admins[].user.id`, and stores/space.ts
    overwrites local state with whatever each mutation returns — so any
    response that includes `space` must include hydrated admins, otherwise
    admin-only UI silently disappears after a PATCH.
    """
    admin_relations = await service.list_admins(space_id)
    admins_list: list[dict] = []
    for rel in admin_relations:
        user = await user_repo.get_by_id(rel.user_id)
        profile = (
            await profile_repo.get_profile_by_user_id(rel.user_id) if user else None
        )
        user_info = (
            {
                "id": user.id,
                "username": user.username,
                "nickname": profile.nickname if profile else user.username,
                "avatarId": profile.avatar_id if profile else None,
                "intro": profile.intro if profile else "",
            }
            if user
            else {"id": rel.user_id, "username": "unknown"}
        )
        admins_list.append(_admin_to_api_model(rel, user_info))
    return admins_list


async def _build_full_space_payload(
    space: Space,
    *,
    service: SpaceService,
    db,
) -> dict:
    """Build a Space response dict that matches the frontend Space type.

    Always includes `admins` (hydrated) and `classificationTopics` so that any
    GET/POST/PATCH response is interchangeable from the frontend's perspective
    (its store overwrites local state with the response payload).
    """
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    space_data = _space_to_api_model(space)
    space_data["admins"] = await _build_admins_payload(
        space.id, service=service, user_repo=user_repo, profile_repo=profile_repo
    )
    topics = await service.list_classification_topics(space.id)
    space_data["classificationTopics"] = [{"id": t.id, "name": t.name} for t in topics]
    return space_data


@router.get(
    "/{spaceId}",
    summary="Query Space",
)
async def get_space(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    queryMyRank: bool = Query(default=False),
    queryCategories: bool = Query(default=False),
    queryClassificationTopics: bool = Query(default=False),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    space = await service.get_space(space_id=space_id)
    if space is None:
        raise NotFoundError(
            "Resource space not found", data={"type": "space", "id": space_id}
        )

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
        profile = (
            await profile_repo.get_profile_by_user_id(rel.user_id) if user else None
        )
        user_info = (
            {
                "id": user.id,
                "username": user.username,
                "nickname": profile.nickname if profile else user.username,
                "avatarId": profile.avatar_id if profile else None,
                "intro": profile.intro if profile else "",
            }
            if user
            else {"id": rel.user_id, "username": "unknown"}
        )
        admins_list.append(_admin_to_api_model(rel, user_info))

    space_data = _space_to_api_model(space)
    space_data["admins"] = admins_list
    # Frontend's stores/space.ts always passes queryClassificationTopics=true
    # and reads space.classificationTopics directly. Always populate it (cheap)
    # so callers that forget the flag still get a sensible value.
    topics = await service.list_classification_topics(space_id)
    space_data["classificationTopics"] = [{"id": t.id, "name": t.name} for t in topics]
    _ = queryClassificationTopics  # Accepted for parity with NT API but always populated.  # noqa: E501

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

    space_ids = [s.id for s in spaces]
    topics_by_space = await service.list_classification_topics_for_spaces(space_ids)

    items: list[dict] = []
    for s in spaces:
        dto = _space_to_api_model(s)
        if queryMyRank:
            dto["myRank"] = await service.get_user_rank(s.id, viewer_id)

        admin_relations = await service.list_admins(s.id)
        admins_list = []
        for rel in admin_relations:
            user = await user_repo.get_by_id(rel.user_id)
            profile = (
                await profile_repo.get_profile_by_user_id(rel.user_id) if user else None
            )
            user_info = (
                {
                    "id": user.id,
                    "username": user.username,
                    "nickname": profile.nickname if profile else user.username,
                    "avatarId": profile.avatar_id if profile else None,
                    "intro": profile.intro if profile else "",
                }
                if user
                else {"id": rel.user_id, "username": "unknown"}
            )
            admins_list.append(_admin_to_api_model(rel, user_info))
        dto["admins"] = admins_list
        dto["classificationTopics"] = [
            {"id": t.id, "name": t.name} for t in topics_by_space.get(s.id, [])
        ]

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
    payload: CreateSpaceRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    if await service.exists_by_name(payload.name):
        raise ConflictError(f"Space with name '{payload.name}' already exists")

    announcements = _expect_list(payload.announcements, "announcements")
    task_templates = _expect_list(payload.task_templates, "taskTemplates")
    classification_topic_ids: list[int] = payload.classification_topics or []

    space = await service.create_space(
        name=payload.name,
        intro=payload.intro,
        description=payload.description,
        avatar_id=payload.avatar_id,
        enable_rank=payload.enable_rank,
        owner_id=auth_user.user_id,
        announcements=announcements,
        task_templates=task_templates,
        visible_task_limit=payload.visible_task_limit,
    )
    if classification_topic_ids:
        await service.replace_classification_topics(
            space_id=space.id,
            topic_ids=classification_topic_ids,
            actor_user_id=auth_user.user_id,
        )
    space_data = await _build_full_space_payload(space, service=service, db=db)
    return {
        "code": 201,
        "message": "Created",
        "data": {"space": space_data},
    }


@router.patch(
    "/{spaceId}",
    summary="Update Space",
)
async def patch_space(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    payload: PatchSpaceRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    announcements = payload.announcements
    task_templates = payload.task_templates
    if announcements is not None:
        announcements = _expect_list(announcements, "announcements")
    if task_templates is not None:
        task_templates = _expect_list(task_templates, "taskTemplates")

    classification_topic_ids: list[int] | None = payload.classification_topics

    space = await service.update_space(
        space_id=space_id,
        actor_user_id=auth_user.user_id,
        name=payload.name,
        intro=payload.intro,
        description=payload.description,
        avatar_id=payload.avatar_id,
        enable_rank=payload.enable_rank,
        announcements=announcements,
        task_templates=task_templates,
        default_category_id=payload.default_category_id,
        visible_task_limit=payload.visible_task_limit,
        set_visible_task_limit="visible_task_limit" in payload.model_fields_set,
    )
    if classification_topic_ids is not None:
        await service.replace_classification_topics(
            space_id=space_id,
            topic_ids=classification_topic_ids,
            actor_user_id=auth_user.user_id,
        )
    space_data = await _build_full_space_payload(space, service=service, db=db)
    return {"code": 200, "message": "OK", "data": {"space": space_data}}


@router.delete(
    "/{spaceId}",
    summary="Delete Space",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_space(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> dict:
    _ = auth_user
    cats = await service.list_categories(
        space_id=space_id, include_archived=includeArchived
    )
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
    sortBy: str = Query(default="publishedAt"),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsService = Depends(get_space_analytics_service),
) -> dict:
    _ = auth_user
    data = await service.get_publishers_participation(space_id=space_id)
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/participants/export",
    summary="Export Space Participants",
    deprecated=True,
    description="Deprecated: use GET /spaces/{spaceId}/analytics/participants/export instead.",  # noqa: E501
)
async def export_space_participants(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    format: str = Query(default="csv"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsService = Depends(get_space_analytics_service),
) -> Response:
    _ = auth_user
    if format.lower() != "csv":
        raise BadRequestError("Only csv format is supported")
    csv_payload = await service.export_participants(space_id=space_id)
    return Response(
        content=csv_payload,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=space-{space_id}-participants.csv"  # noqa: E501
        },
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
            "Content-Disposition": f"attachment; filename=space-{space_id}-participants.csv"  # noqa: E501
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
            "Content-Disposition": f'attachment; filename="space-{space_id}-publishers.csv"'  # noqa: E501
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
    service: SpaceMemberPublishingService = Depends(
        get_space_member_publishing_service
    ),
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
    service: SpaceMemberPublishingService = Depends(
        get_space_member_publishing_service
    ),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceTagsService = Depends(get_space_topics_service),
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
    payload: CreateSpaceCategoryRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> dict:
    category = await service.create_category(
        space_id=space_id,
        name=payload.name,
        description=payload.description,
        display_order=payload.display_order,
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
    payload: PatchSpaceCategoryRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> dict:
    # Support both "archived" (boolean) and "archivedAt" (timestamp)
    archived = payload.archived
    if archived is None and payload.archived_at is not None:
        archived = payload.archived_at > 0

    category = await service.update_category(
        space_id=space_id,
        category_id=category_id,
        actor_user_id=auth_user.user_id,
        name=payload.name,
        description=payload.description,
        display_order=payload.display_order,
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> dict:
    _ = auth_user
    category = await service.get_category_detail(
        space_id=space_id, category_id=category_id
    )
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    "/{spaceId}/domain-groups",
    summary="List Space Domain Groups",
)
async def list_space_domain_groups(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> dict:
    groups = await service.list_domain_groups(
        space_id=space_id, actor_user_id=auth_user.user_id
    )
    items = [_domain_group_to_api_model(group, domains) for group, domains in groups]
    return {
        "code": 200,
        "message": "OK",
        "data": {"groups": items},
    }


@router.post(
    "/{spaceId}/domain-groups",
    summary="Create Space Domain Group",
    status_code=status.HTTP_201_CREATED,
)
async def create_space_domain_group(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    payload: CreateSpaceDomainGroupRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> dict:
    group, domains = await service.create_domain_group(
        space_id=space_id,
        name=payload.name,
        description=payload.description,
        domains=payload.domains,
        actor_user_id=auth_user.user_id,
    )
    return {
        "code": 201,
        "message": "Created",
        "data": {"group": _domain_group_to_api_model(group, domains)},
    }


@router.patch(
    "/{spaceId}/domain-groups/{groupId}",
    summary="Update Space Domain Group",
)
async def patch_space_domain_group(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    group_id: Annotated[int, Path(ge=1, alias="groupId")],
    payload: PatchSpaceDomainGroupRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> dict:
    group, domains = await service.update_domain_group(
        space_id=space_id,
        group_id=group_id,
        name=payload.name,
        description=payload.description,
        domains=payload.domains,
        actor_user_id=auth_user.user_id,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {"group": _domain_group_to_api_model(group, domains)},
    }


@router.delete(
    "/{spaceId}/domain-groups/{groupId}",
    summary="Delete Space Domain Group",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_space_domain_group(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    group_id: Annotated[int, Path(ge=1, alias="groupId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> None:
    await service.delete_domain_group(
        space_id=space_id,
        group_id=group_id,
        actor_user_id=auth_user.user_id,
    )
    return None


@router.get(
    "/{spaceId}/managers",
    summary="List Space Managers",
)
async def list_space_admins(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> dict:
    _ = auth_user
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
    payload: AddSpaceManagerRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    role_value = payload.role.upper()
    role_mapping = {"OWNER": SpaceAdminRole.OWNER, "ADMIN": SpaceAdminRole.ADMIN}
    role = role_mapping.get(role_value)
    if role is None:
        raise BadRequestError(f"Invalid role: {role_value}")
    await service.add_admin(
        space_id=space_id,
        target_user_id=payload.user_id,
        role=role,
        actor_user_id=auth_user.user_id,
    )
    space = await service.get_space(space_id)
    if space is None:
        return {"code": 201, "message": "Created", "data": None}
    space_data = await _build_full_space_payload(space, service=service, db=db)
    return {"code": 201, "message": "Created", "data": {"space": space_data}}


@router.delete(
    "/{spaceId}/managers/{userId}",
    summary="Remove Space Manager",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_space_admin(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    payload: PatchSpaceManagerRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    role_map = {"OWNER": SpaceAdminRole.OWNER, "ADMIN": SpaceAdminRole.ADMIN}
    new_role = role_map.get(payload.role.upper())
    if new_role is None:
        raise BadRequestError(f"Invalid role: {payload.role}. Must be OWNER or ADMIN")
    await service.update_admin_role(
        space_id=space_id,
        target_user_id=user_id,
        new_role=new_role,
        actor_user_id=auth_user.user_id,
    )
    space = await service.get_space(space_id)
    if space is None:
        return {"code": 200, "message": "OK", "data": None}
    space_data = await _build_full_space_payload(space, service=service, db=db)
    return {"code": 200, "message": "OK", "data": {"space": space_data}}
