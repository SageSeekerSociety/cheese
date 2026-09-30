"""How a space is organized, and who runs it.

A slice of `app/api/routes/spaces.py` (arch review, tracking issue #2143): the
tail of the file -- `GET /spaces/{spaceId}/topics`, the six `/categories`
routes, the four `/domain-groups` routes and the four `/managers` routes -- one
concept. Every route in it reads or writes the space's own organizing
structures: what the board is about, how its work is grouped into categories
and domain groups, and who its managers are. It is a *suffix* of that file's
route list, so moving it leaves every path, method, `operationId` and their
order exactly where they were, and the module mounts itself through
`app.main._discover_routers` like every other route module.

The helpers it shares with the routes that stay -- `_category_to_api_model`,
`_domain_group_to_api_model`, `_admin_to_api_model`, `_build_full_space_payload`,
`_ensure_space_visible`, `get_space_service`, `get_space_topics_service` -- stay
in `spaces.py` and are imported here, the shape `users_password.py` and
`topics_side_routes.py` already use. `spaces.py` imports nothing from this
module, so there is no cycle.

`SpaceAdminRole` is read here but declared in `app.domain.space.models`, in
`app.domain.*.models` -- the modules C2 (`routes-touch-no-models` in
`.importlinter`) forbids a route module from importing directly. It is imported
**from `spaces.py`**, which already carries that frozen import, rather than from
the model module: the same shape `topics_side_routes.py` uses for
`BlockRepository`, and it adds no line to the C2 baseline. The six request
bodies the moved routes name are named nowhere else, so they move with them.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.api.routes.spaces import (
    AddSpaceManagerRequest,
    CreateSpaceCategoryRequest,
    CreateSpaceDomainGroupRequest,
    PatchSpaceCategoryRequest,
    PatchSpaceDomainGroupRequest,
    PatchSpaceManagerRequest,
    SpaceAdminRole,
    _admin_to_api_model,
    _build_full_space_payload,
    _category_to_api_model,
    _domain_group_to_api_model,
    _ensure_space_visible,
    get_space_service,
    get_space_topics_service,
    require_reviewed_space,
)
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError
from app.db.session import get_db
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
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
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
        # `model_dump()` (field names, not aliases): what lands in the column is
        # the shape `Teaching.from_json` reads back, so the write path and the
        # read path cannot drift into two different spellings of one config.
        teaching=(
            payload.teaching.model_dump() if payload.teaching is not None else None
        ),
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
    db=Depends(get_db),
) -> dict:
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
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
    db=Depends(get_db),
) -> dict:
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
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
    db=Depends(get_db),
) -> dict:
    # The managers list answers only to someone who can see the 题目版 — an
    # outsider must not learn who runs a board they were never invited to.
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
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
