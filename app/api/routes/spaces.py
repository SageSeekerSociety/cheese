from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.auth.checker import get_auth_user, require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.db.session import get_db
from app.domain.space.analytics_service import SpaceAnalyticsService
from app.domain.space.models import Space, SpaceAdminRelation, SpaceAdminRole, SpaceCategory
from app.domain.space.repositories import (
    SpaceAdminRelationRepository,
    SpaceCategoryRepository,
    SpaceRepository,
    SpaceUserRankRepository,
)
from app.domain.space.services import SpaceService
from app.domain.task.repositories import TaskMembershipRepository, TaskRepository
from app.domain.user.repositories import UserProfileRepository, UserRepository

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


def _admin_to_api_model(rel: SpaceAdminRelation) -> dict:
    created_at_ms = int(rel.created_at.timestamp() * 1000) if rel.created_at else 0
    role_name_map = {SpaceAdminRole.OWNER.value: "OWNER", SpaceAdminRole.ADMIN.value: "ADMIN"}
    return {
        "userId": rel.user_id,
        "role": role_name_map.get(rel.role, "ADMIN"),
        "createdAt": created_at_ms,
    }


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

    data = {
        "space": _space_to_api_model(space),
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
) -> dict:
    offset = pageStart or 0
    spaces = await service.list_spaces(limit=pageSize, offset=offset)
    total = await service.count_spaces()
    viewer_id = auth_user.user_id if auth_user.user_id > 0 else None

    items: list[dict] = []
    for s in spaces:
        dto = _space_to_api_model(s)
        if queryMyRank:
            dto["myRank"] = await service.get_user_rank(s.id, viewer_id)
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
    categoryId: int | None = Query(default=None),
    taskStatus: str | None = Query(default=None),
    publisherId: int | None = Query(default=None),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: SpaceAnalyticsService = Depends(get_space_analytics_service),
) -> dict:
    _ = auth_user
    data = await service.get_task_analytics(
        space_id=space_id,
        category_id=categoryId,
        task_status=taskStatus,
        publisher_id=publisherId,
    )
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/publishers/participation",
    summary="Get Publishers Participation",
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
