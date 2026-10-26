import json
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Mapped

from app.api.response import ok
from app.api.space_deps import (
    get_space_analytics_service,
    get_space_analytics_view_service,
    get_space_service,
    require_reviewed_space,
)
from app.api.space_payloads import (
    SpaceInviteCodeRow,
    TeachingRequest,
    _admin_to_api_model,
    _build_full_space_payload,
    _category_to_api_model,
    _hydrate_people,
    _invite_codes_to_api_models,
    _space_to_api_model,
)
from app.api.write_access import ROUTE_DECIDES, needs
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.auth.space_access import is_space_admin
from app.core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.db.session import get_db
from app.domain.space.analytics_service import SpaceAnalyticsService
from app.domain.space.analytics_view_service import SpaceAnalyticsViewService
from app.domain.space.permissions import Permission as SpacePermission
from app.domain.space.services import SpaceService
from app.domain.user.repositories import (
    UserProfileRepository,
    UserRepository,
)
from app.domain.user.services import languages_by_ids

# ── Rows handed back by the space domain ──────────────────────────────────────
#
# `SpaceService` returns rows this route only reads attributes off. Naming their
# shape here — rather than importing `app.domain.space.models` — keeps the route
# out of the space domain's ORM models, which the architecture guard (C2)
# forbids an API module from reaching into. Only the attributes read below are
# named, mirroring `_SessionRow` in routes/users_sessions.py.


class SpaceMemberRow(Protocol):
    user_id: Mapped[int]
    invite_code_id: Mapped[int | None]
    created_at: Mapped[datetime]


# ── Request Models ────────────────────────────────────────────────────────────


class CreateSpaceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=255)
    intro: str = ""
    description: str = ""
    avatar_id: int | None = Field(default=None, alias="avatarId")
    enable_rank: bool = Field(default=False, alias="enableRank")
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
    task_templates: list | str | None = Field(default=None, alias="taskTemplates")
    classification_topics: list[int] | None = Field(
        default=None, alias="classificationTopics"
    )
    default_category_id: int | None = Field(default=None, alias="defaultCategoryId")
    visible_task_limit: int | None = Field(default=None, alias="visibleTaskLimit")
    #: 空间级「给 AI 队友的指导」(#944) — the board-wide default every 题目 on
    #: it starts from. Same shape and same whole-key semantics as the 项目集's
    #: `teaching`: sending it replaces the WHOLE config, omitting it leaves it
    #: exactly as it is.
    teaching: TeachingRequest | None = None

    @field_validator("visible_task_limit", mode="before")
    @classmethod
    def _validate_visible_task_limit(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("visibleTaskLimit must be null or a non-negative integer")
        return value


class JoinSpaceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str = Field(..., min_length=1)


class AddSpaceMemberRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: int = Field(..., alias="userId", gt=0)


router = APIRouter(
    prefix="/spaces", tags=["Spaces"], dependencies=[Depends(require_reviewed_space)]
)


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


def _member_to_api_model(
    member: SpaceMemberRow,
    user_info: dict | None = None,
    invite_code: SpaceInviteCodeRow | None = None,
) -> dict:
    """One member row of the 成员 page.

    ``inviteCode`` is the 「加入方式」 column: the code this membership came in
    on, or **null**, which is the answer for two cases the row cannot tell
    apart — a member who predates the column (nobody recorded the code) and one
    the owner added directly (no code was involved). The screen renders both as
    未知: null means "not on record", and dressing it up as a blank or a 0
    would claim something the row does not say.
    """
    joined_at_ms = int(member.created_at.timestamp() * 1000) if member.created_at else 0
    result: dict = {"userId": member.user_id, "joinedAt": joined_at_ms}
    if user_info is not None:
        result["user"] = user_info
    result["inviteCode"] = (
        {"id": invite_code.id, "code": invite_code.code}
        if invite_code is not None
        else None
    )
    return result


async def _hydrate_members(
    members: Sequence[SpaceMemberRow],
    *,
    user_repo: UserRepository,
    profile_repo: UserProfileRepository,
    space_service: SpaceService,
) -> list[dict]:
    """Roster rows, with the people and the codes they came in on.

    Both lookups are batched for the same reason, and it is the same reason the
    people half was batched before: a per-row lookup makes rendering a board
    cost grow with the class. The codes are fetched through
    ``get_by_ids``, which deliberately keeps soft-deleted codes — 加入方式 has
    to keep naming the code that let someone in after it is revoked, and that
    is precisely when somebody goes looking at this column.
    """
    people = await _hydrate_people(
        [member.user_id for member in members],
        user_repo=user_repo,
        profile_repo=profile_repo,
    )
    codes = await space_service.invite_codes_by_ids(
        [m.invite_code_id for m in members if m.invite_code_id is not None]
    )
    return [
        _member_to_api_model(
            member,
            people[member.user_id],
            codes.get(member.invite_code_id) if member.invite_code_id else None,
        )
        for member in members
    ]


@router.get(
    "/{spaceId}",
    summary="Query Space",
    dependencies=[needs(SpacePermission.view, on="spaceId")],
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

    # 私有指导只对这块板的管理员可见（#944 的第二种可见性）。一次判定同时决定空间
    # 与项目集两处的 `teaching` —— 一个屏幕上的两份配置要么都看得到，要么都看不到。
    may_manage = auth_user.user_id > 0 and await is_space_admin(
        session=db, space_id=space_id, user_id=auth_user.user_id
    )

    categories = None
    if queryCategories:
        cats = await service.list_categories(space_id=space_id, include_archived=False)
        categories = [
            _category_to_api_model(c, include_private=may_manage) for c in cats
        ]

    my_rank = None
    viewer_id = auth_user.user_id if auth_user.user_id > 0 else None
    if queryMyRank:
        my_rank = await service.get_user_rank(space_id, viewer_id)

    # admins / classificationTopics 都由这一个构建器给齐：前端拿到任何
    # 一份 Space 响应都能直接用（它的 store 会用响应覆盖本地状态）。
    space_data = await _build_full_space_payload(
        space, service=service, db=db, include_private=may_manage
    )
    _ = queryClassificationTopics  # Accepted for parity with NT API but always populated.

    data: dict = {
        "space": space_data,
        "categories": categories,
    }
    if queryMyRank:
        data["myRank"] = my_rank
    return ok(data)


@router.get(
    "",
    summary="Enumerate Spaces",
)
async def get_spaces(
    queryMyRank: bool = Query(default=False),
    pageStart: int | None = Query(default=None, ge=0),
    pageSize: int = Query(default=20, ge=1, le=200),
    service: SpaceService = Depends(get_space_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    offset = pageStart or 0
    viewer_id = auth_user.user_id if auth_user.user_id > 0 else None
    # A 题目版 this viewer is not in is not merely hidden from
    # the page — it is not in the page, because the same predicate the detail
    # route refuses by is the one that removes the row here.
    spaces = await service.list_spaces(
        limit=pageSize, offset=offset, member_user_id=auth_user.user_id
    )
    total = await service.count_spaces(member_user_id=auth_user.user_id)

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
        # 一次取齐所有管理员的人；头像判据（挑过没有）也在 _hydrate_people 里。
        people = await _hydrate_people(
            [rel.user_id for rel in admin_relations],
            user_repo=user_repo,
            profile_repo=profile_repo,
        )
        dto["admins"] = [
            _admin_to_api_model(rel, people[rel.user_id]) for rel in admin_relations
        ]
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
    return ok({"spaces": items, "page": page})


@router.post(
    "",
    summary="Create Space",
    status_code=status.HTTP_201_CREATED,
    dependencies=[ROUTE_DECIDES],
)
async def create_space(
    payload: CreateSpaceRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    if await service.exists_by_name(payload.name):
        raise ConflictError(f"Space with name '{payload.name}' already exists")

    task_templates = _expect_list(payload.task_templates, "taskTemplates")
    classification_topic_ids: list[int] = payload.classification_topics or []

    languages = await languages_by_ids(db, [auth_user.user_id])
    space = await service.create_space(
        name=payload.name,
        intro=payload.intro,
        description=payload.description,
        avatar_id=payload.avatar_id,
        enable_rank=payload.enable_rank,
        owner_id=auth_user.user_id,
        task_templates=task_templates,
        visible_task_limit=payload.visible_task_limit,
        language=languages.get(auth_user.user_id),
    )
    if classification_topic_ids:
        await service.replace_classification_topics(
            space_id=space.id,
            topic_ids=classification_topic_ids,
        )
    space.review_status = "PENDING"
    await db.flush()
    # 写到这里就完了，下面全是读：先提交，再构造响应。``get_db`` 的提交在
    # ``yield`` 的退出码里，而那段跑在响应发出**之后**（FastAPI 0.137 的
    # ``request_stack`` 在 ``await response(...)`` 之后才关），不在这里提交的话，
    # 客户端拿到 201 时这一行还没落地 —— 紧接着的 POST /admin/spaces/{id}/review
    # 就会 404「Space not found」（合并队列 run 36296605673 实测）。
    await db.commit()
    space_data = await _build_full_space_payload(
        space, service=service, db=db, include_private=True
    )
    # Every 题目版 is created holding a code (see SpaceService.create_space),
    # so hand it back here rather than making the creator come and ask.
    codes = await service.list_invite_codes(space_id=space.id)
    # Same shape the 邀请码 screen reads, its maker hydrated and all: the
    # creator is looking at this payload right after the board appears, and a
    # code that arrives without the same fields the list gives it is how two
    # views of one row drift apart.
    items = await _invite_codes_to_api_models(codes, db=db)
    return ok(
        {
            "space": space_data,
            "inviteCode": items[0] if items else None,
        }
    )


@router.patch(
    "/{spaceId}",
    summary="Update Space",
    dependencies=[needs(SpacePermission.edit_settings, on="spaceId")],
)
async def patch_space(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    payload: PatchSpaceRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    task_templates = payload.task_templates
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
        task_templates=task_templates,
        default_category_id=payload.default_category_id,
        visible_task_limit=payload.visible_task_limit,
        set_visible_task_limit="visible_task_limit" in payload.model_fields_set,
        # `model_dump()` (field names, not aliases): what lands in the column is
        # the shape `Teaching.from_json` reads back, so the write path and the
        # read path cannot drift into two spellings of one config — the same
        # rule the 项目集 PATCH follows.
        teaching=(
            payload.teaching.model_dump() if payload.teaching is not None else None
        ),
    )
    if classification_topic_ids is not None:
        await service.replace_classification_topics(
            space_id=space_id,
            topic_ids=classification_topic_ids,
        )
    space_data = await _build_full_space_payload(
        space, service=service, db=db, include_private=True
    )
    return ok({"space": space_data})


@router.delete(
    "/{spaceId}",
    summary="Delete Space",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[needs(SpacePermission.delete, on="spaceId")],
)
async def delete_space(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> None:
    await service.delete_space(space_id=space_id)
    return None


@router.post(
    "/join", summary="Join a Space with an invite code", dependencies=[ROUTE_DECIDES]
)
async def join_space(
    payload: JoinSpaceRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    if auth_user.user_id <= 0:
        raise ForbiddenError("Only signed-in users can join a space")
    space = await service.join_space(
        code=payload.code.strip(), user_id=auth_user.user_id
    )
    # Commit before answering: someone told they have joined opens the board on
    # their next request, and the board is closed to non-members. Same reason as
    # the commit in ``create_space``.
    await db.commit()
    space_data = await _build_full_space_payload(space, service=service, db=db)
    return ok({"space": space_data})


@router.get(
    "/{spaceId}/members",
    summary="List Space Members",
    dependencies=[needs(SpacePermission.view, on="spaceId")],
)
async def list_space_members(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    members = await service.list_members(space_id)
    items = await _hydrate_members(
        members,
        user_repo=UserRepository(session=db),
        profile_repo=UserProfileRepository(session=db),
        space_service=service,
    )
    return ok({"members": items})


@router.post(
    "/{spaceId}/members",
    summary="Add Space Member",
    status_code=status.HTTP_201_CREATED,
    dependencies=[needs(SpacePermission.manage_members, on="spaceId")],
)
async def add_space_member(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    payload: AddSpaceMemberRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    # Visibility first: otherwise an outsider probing this route is told 403,
    # which confirms the 题目版 exists — the thing a 404 is here to avoid.
    user_repo = UserRepository(session=db)
    if await user_repo.get_by_id(payload.user_id) is None:
        raise NotFoundError(
            "User not found", data={"type": "user", "id": payload.user_id}
        )
    member = await service.add_member(
        space_id=space_id,
        target_user_id=payload.user_id,
    )
    items = await _hydrate_members(
        [member],
        user_repo=user_repo,
        profile_repo=UserProfileRepository(session=db),
        space_service=service,
    )
    return ok({"member": items[0]})


@router.delete(
    "/{spaceId}/members/{userId}",
    summary="Remove Space Member",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[needs(SpacePermission.manage_members, on="spaceId")],
)
async def delete_space_member(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> Response:
    await service.remove_member(
        space_id=space_id,
        target_user_id=user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{spaceId}/leave",
    summary="Leave a Space",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[ROUTE_DECIDES],
)
async def leave_space(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> Response:
    await service.leave_space(space_id=space_id, user_id=auth_user.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{spaceId}/analytics/tasks",
    summary="Get Space Task Analytics",
    dependencies=[needs(SpacePermission.view_analytics, on="spaceId")],
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
    # ``createdAt``, not ``publishedAt``: this route sorts inside
    # ``SpaceAnalyticsViewService`` against ``TASK_SORT_FIELDS``, which has no
    # ``publishedAt`` key (``/tasks`` maps that name onto ``createdAt`` first —
    # this layer never did). A default outside the set 400s every caller that
    # takes the documented default, which is exactly what it used to do.
    sortBy: str = Query(default="createdAt"),
    sortOrder: str = Query(default="desc"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsViewService = Depends(get_space_analytics_view_service),
    db=Depends(get_db),
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
    return ok(data)


@router.get(
    "/{spaceId}/publishers/participation",
    summary="Get Publishers Participation",
    deprecated=True,
    description="Deprecated: use GET /spaces/{spaceId}/analytics/publishers instead.",
    dependencies=[needs(SpacePermission.view_analytics, on="spaceId")],
)
async def get_publishers_participation(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsService = Depends(get_space_analytics_service),
    db=Depends(get_db),
) -> dict:
    _ = auth_user
    data = await service.get_publishers_participation(space_id=space_id)
    return ok(data)


@router.get(
    "/{spaceId}/participants/export",
    summary="Export Space Participants",
    deprecated=True,
    description="Deprecated: use GET /spaces/{spaceId}/analytics/participants/export instead.",
    dependencies=[needs(SpacePermission.view_analytics, on="spaceId")],
)
async def export_space_participants(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    format: str = Query(default="csv"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsService = Depends(get_space_analytics_service),
    db=Depends(get_db),
) -> Response:
    _ = auth_user
    if format.lower() != "csv":
        raise BadRequestError("Only csv format is supported")
    csv_payload = await service.export_participants(space_id=space_id)
    return Response(
        content=csv_payload,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=space-{space_id}-participants.csv"
        },
    )
