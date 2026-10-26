"""How a space is organized, and who runs it.

A slice of `app/api/routes/spaces.py` (arch review, tracking issue #2143): the
tail of the file -- `GET /spaces/{spaceId}/topics`, the `/categories` routes,
the four `/domain-groups` routes and the four `/managers` routes -- one
concept. Every route in it reads or writes the space's own organizing
structures: what the board is about, how its work is grouped into categories
and domain groups, and who its managers are. The block was a *suffix* of that
file's route list when #2198 moved it, so that move left every path, method,
`operationId` and their order exactly where they were. Slice 4 (#2143) then
moved the last category route, `GET /spaces/{spaceId}/categories`, here from
the file's legacy NT-API tail, so that all seven category routes are read in one
module: it keeps its path, method and `operationId` and only changes its index
in the openapi `paths` map, and it needs no import this module does not already
carry. The module mounts itself through `app.main._discover_routers` like every
other route module.

The helpers it shares with the routes that stay -- `_category_to_api_model`,
`_domain_group_to_api_model`, `_admin_to_api_model`, `_build_full_space_payload`,
`get_space_service`, `get_space_topics_service` -- stay
in `spaces.py` and are imported here, the shape `users_password.py` and
`topics_side_routes.py` already use. `spaces.py` imports nothing from this
module, so there is no cycle.

A client-supplied role name (`OWNER` / `ADMIN`) is turned into the value
`add_admin` / `update_admin_role` expect by `SpaceService.admin_role_from_name`
rather than by reading `SpaceAdminRole` here: that enum lives in
`app.domain.space.models`, which C2 (`routes-touch-no-models` in
`.importlinter`) forbids a route module from importing. The six request bodies
the moved routes name are defined in `spaces.py`'s shared Request Models block
and are imported from there, like the helpers.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.api.response import ok
from app.api.space_deps import (
    get_space_service,
    get_space_topics_service,
    require_reviewed_space,
)
from app.api.space_payloads import (
    AddSpaceManagerRequest,
    CreateSpaceCategoryRequest,
    CreateSpaceDomainGroupRequest,
    PatchSpaceCategoryRequest,
    PatchSpaceDomainGroupRequest,
    PatchSpaceManagerRequest,
    _admin_to_api_model,
    _build_full_space_payload,
    _category_to_api_model,
    _domain_group_to_api_model,
)
from app.api.write_access import needs
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError
from app.db.session import get_db
from app.domain.space import permissions as space_seats
from app.domain.space.permissions import Permission as SpacePermission
from app.domain.space.services import SpaceService
from app.domain.space.tags_service import SpaceTagsService

router = APIRouter(
    prefix="/spaces", tags=["Spaces"], dependencies=[Depends(require_reviewed_space)]
)


# ---------------------------------------------------------------------------
# Space Topics (NT-API aligned)
# ---------------------------------------------------------------------------


@router.get(
    "/{spaceId}/topics",
    summary="List or search topics in a space",
    dependencies=[needs(SpacePermission.view, on="spaceId")],
)
async def get_space_topics(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    keyword: str | None = Query(default=None),
    sort: str = Query(default="name"),
    limit: int = Query(default=20, ge=1, le=100),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceTagsService = Depends(get_space_topics_service),
    db=Depends(get_db),
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

    return ok({"topics": topics})


@router.get(
    "/{spaceId}/categories",
    summary="List categories in a space",
    dependencies=[needs(SpacePermission.view, on="spaceId")],
)
async def list_space_categories(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    includeArchived: bool = Query(default=False),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    _ = auth_user
    cats = await service.list_categories(
        space_id=space_id, include_archived=includeArchived
    )
    # 私有指导只对这块板的管理员可见（#944 第二种可见性）：成员读到的是公开那一份。
    may_manage = auth_user.user_id > 0 and await space_seats.runs(
        db, space_id, auth_user.user_id
    )
    items = [_category_to_api_model(c, include_private=may_manage) for c in cats]
    return ok({"categories": items})


@router.post(
    "/{spaceId}/categories",
    summary="Create Space Category",
    status_code=status.HTTP_201_CREATED,
    dependencies=[needs(SpacePermission.edit_settings, on="spaceId")],
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
        # 项目集级默认队友 (#944)：与 PATCH 同一条入口语义。
        default_role=payload.default_role,
    )
    return ok({"category": _category_to_api_model(category, include_private=True)})


@router.patch(
    "/{spaceId}/categories/{categoryId}",
    summary="Update Space Category",
    dependencies=[needs(SpacePermission.edit_settings, on="spaceId")],
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
        name=payload.name,
        description=payload.description,
        display_order=payload.display_order,
        archived=archived,
        # 项目集级默认队友 (#944)：整键语义 —— 省掉 = 不动，空串 = 清掉这一格。
        default_role=payload.default_role,
    )
    return ok({"category": _category_to_api_model(category, include_private=True)})


@router.get(
    "/{spaceId}/categories/{categoryId}",
    summary="Get Space Category",
    dependencies=[needs(SpacePermission.view, on="spaceId")],
)
async def get_space_category(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    category_id: Annotated[int, Path(ge=1, alias="categoryId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    category = await service.get_category_detail(
        space_id=space_id, category_id=category_id
    )
    may_manage = auth_user.user_id > 0 and await space_seats.runs(
        db, space_id, auth_user.user_id
    )
    return ok(
        {"category": _category_to_api_model(category, include_private=may_manage)}
    )


@router.delete(
    "/{spaceId}/categories/{categoryId}",
    summary="Delete Space Category",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[needs(SpacePermission.edit_settings, on="spaceId")],
)
async def delete_space_category(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    category_id: Annotated[int, Path(ge=1, alias="categoryId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> None:
    await service.delete_category(
        space_id=space_id,
        category_id=category_id,
    )
    return None


@router.post(
    "/{spaceId}/categories/{categoryId}/archive",
    summary="Archive Space Category",
    dependencies=[needs(SpacePermission.edit_settings, on="spaceId")],
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
    )
    return ok({"category": _category_to_api_model(category, include_private=True)})


@router.delete(
    "/{spaceId}/categories/{categoryId}/archive",
    summary="Unarchive Space Category",
    dependencies=[needs(SpacePermission.edit_settings, on="spaceId")],
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
    )
    return ok({"category": _category_to_api_model(category, include_private=True)})


@router.get(
    "/{spaceId}/domain-groups",
    summary="List Space Domain Groups",
    dependencies=[needs(SpacePermission.view, on="spaceId")],
)
async def list_space_domain_groups(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    groups = await service.list_domain_groups(space_id=space_id)
    items = [_domain_group_to_api_model(group, domains) for group, domains in groups]
    return ok({"groups": items})


@router.post(
    "/{spaceId}/domain-groups",
    summary="Create Space Domain Group",
    status_code=status.HTTP_201_CREATED,
    dependencies=[needs(SpacePermission.edit_settings, on="spaceId")],
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
    )
    return ok({"group": _domain_group_to_api_model(group, domains)})


@router.patch(
    "/{spaceId}/domain-groups/{groupId}",
    summary="Update Space Domain Group",
    dependencies=[needs(SpacePermission.edit_settings, on="spaceId")],
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
    )
    return ok({"group": _domain_group_to_api_model(group, domains)})


@router.delete(
    "/{spaceId}/domain-groups/{groupId}",
    summary="Delete Space Domain Group",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[needs(SpacePermission.edit_settings, on="spaceId")],
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
    )
    return None


@router.get(
    "/{spaceId}/managers",
    summary="List Space Managers",
    dependencies=[needs(SpacePermission.view, on="spaceId")],
)
async def list_space_admins(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    # The managers list answers only to someone who can see the 题目版 — an
    # outsider must not learn who runs a board they were never invited to.
    admins = await service.list_admins(space_id)
    return ok({"managers": [_admin_to_api_model(rel) for rel in admins]})


@router.post(
    "/{spaceId}/managers",
    summary="Add Space Manager",
    status_code=status.HTTP_201_CREATED,
    dependencies=[needs(SpacePermission.manage_admins, on="spaceId")],
)
async def add_space_admin(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    payload: AddSpaceManagerRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    role_value = payload.role.upper()
    role = SpaceService.admin_role_from_name(role_value)
    if role is None:
        raise BadRequestError(f"Invalid role: {role_value}")
    await service.add_admin(
        space_id=space_id,
        target_user_id=payload.user_id,
        role=role,
    )
    space = await service.get_space(space_id)
    if space is None:
        return ok(None)
    space_data = await _build_full_space_payload(space, service=service, db=db)
    return ok({"space": space_data})


@router.delete(
    "/{spaceId}/managers/{userId}",
    summary="Remove Space Manager",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[needs(SpacePermission.manage_admins, on="spaceId")],
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
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/{spaceId}/managers/{userId}",
    summary="Update Space Manager Role",
    dependencies=[needs(SpacePermission.manage_admins, on="spaceId")],
)
async def patch_space_manager(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    payload: PatchSpaceManagerRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    new_role = SpaceService.admin_role_from_name(payload.role)
    if new_role is None:
        raise BadRequestError(f"Invalid role: {payload.role}. Must be OWNER or ADMIN")
    await service.update_admin_role(
        space_id=space_id,
        target_user_id=user_id,
        new_role=new_role,
    )
    space = await service.get_space(space_id)
    if space is None:
        return ok(None)
    space_data = await _build_full_space_payload(space, service=service, db=db)
    return ok({"space": space_data})
