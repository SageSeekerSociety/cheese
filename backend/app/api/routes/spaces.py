import json
import logging
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

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
from app.domain.knowledge.services import KnowledgeService
from app.domain.materials.services import MaterialService
from app.domain.space.analytics_service import SpaceAnalyticsService
from app.domain.space.analytics_view_service import SpaceAnalyticsViewService
from app.domain.space.member_publishing_service import SpaceMemberPublishingService
from app.domain.space.models import (
    Space,
    SpaceAdminRelation,
    SpaceAdminRole,
    SpaceCategory,
    SpaceDomainGroup,
    SpaceInviteCode,
    SpaceMember,
)
from app.domain.space.repositories import (
    SpaceAdminRelationRepository,
    SpaceCategoryRepository,
    SpaceClassificationTopicsRepository,
    SpaceDomainGroupDomainRepository,
    SpaceDomainGroupRepository,
    SpaceInviteCodeRepository,
    SpaceMemberRepository,
    SpaceRepository,
    SpaceUserRankRepository,
)
from app.domain.space.review_service import SpaceReviewService
from app.domain.space.services import SpaceLabels, SpaceService
from app.domain.space.tags_service import SpaceTagsService
from app.domain.task.protocol import Teaching
from app.domain.task.repositories import TaskMembershipRepository, TaskRepository
from app.domain.user.realname_services import UserRealNameService
from app.domain.user.repositories import (
    UserProfileRepository,
    UserRealNameRepository,
    UserRepository,
)

_logger = logging.getLogger(__name__)


# ── Request Models ────────────────────────────────────────────────────────────


class TeachingRequest(BaseModel):
    """给 AI 队友的指导 (#8d772257 项目集级, #944 空间级与题目级) — 最小编辑入口。

    **The strict end of this key.** `Teaching.from_json` on the read path drops a
    bad field rather than raising, because `resolve()` runs on every turn of
    every project and a typo in one field of a 项目集 must not take down the
    twenty 赛题 under it. Here a person is looking at the form and can be told
    which field is wrong, so every field is checked and the ids are typed.

    Defined above the three request models that carry it (空间 PATCH, 项目集
    PATCH, 题目 create/patch) so all of them can name the type directly rather
    than through a forward reference.
    """

    model_config = ConfigDict(populate_by_name=True)

    #: system prompt 模板；`{current_week}` / `{allowed_topics}` /
    #: `{avoid_in_code}` 在里面会被本周的值替换掉。
    system_prompt: str | None = Field(default=None, alias="systemPrompt")
    current_week: int | None = Field(default=None, alias="currentWeek", ge=0)
    allowed_topics: list[str] = Field(default_factory=list, alias="allowedTopics")
    avoid_in_code: list[str] = Field(default_factory=list, alias="avoidInCode")
    #: 课件 / 知识材料的引用。正文不在这里 —— 它们各自有自己的库和接口，这里只
    #: 存指向它们的 id。
    material_ids: list[Annotated[int, Field(gt=0)]] = Field(
        default_factory=list, alias="materialIds"
    )
    knowledge_ids: list[Annotated[int, Field(gt=0)]] = Field(
        default_factory=list, alias="knowledgeIds"
    )


def teaching_to_api(teaching: Teaching) -> dict:
    """The read side of `TeachingRequest`: the same six camelCase keys.

    The column stores snake_case (the spelling `Teaching.from_json` reads), and
    every response that carries a 指导 goes through here rather than returning
    the stored dict: a form fills itself from what it reads and saves that back
    whole, so a read in another spelling shows it blank and the next save erases
    the stored config.
    """
    return {
        "systemPrompt": teaching.system_prompt,
        "currentWeek": teaching.current_week,
        "allowedTopics": list(teaching.allowed_topics),
        "avoidInCode": list(teaching.avoid_in_code),
        "materialIds": list(teaching.material_ids),
        "knowledgeIds": list(teaching.knowledge_ids),
    }


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
    #: 课程级教学配置。Sending it replaces the WHOLE teaching config (the
    #: protocol's whole-key semantics — a half-merged week is harder to reason
    #: about than either version alone); omitting it leaves it exactly as it is,
    #: so a PATCH that only renames a 项目集 does not wipe the 教学安排.
    teaching: TeachingRequest | None = None


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


class JoinSpaceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str = Field(..., min_length=1)


class AddSpaceMemberRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: int = Field(..., alias="userId", gt=0)


class CreateSpaceInviteCodeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    max_uses: int | None = Field(default=None, alias="maxUses")
    expires_at: int | None = Field(default=None, alias="expiresAt")
    # 这张码给谁 / 干什么用. Optional and free text; omitted or blank is stored
    # as NULL, which the read side reports as "no note" rather than "".
    note: str | None = Field(default=None, alias="note")


class PatchSpaceInviteCodeRequest(BaseModel):
    """What may still be changed on a live code.

    Both fields are nullable and both are optional, and the two mean different
    things: an absent ``expiresAt`` leaves the date alone, an explicit null
    clears it. The route reads ``model_fields_set`` to tell them apart.
    ``note`` follows the same rule: absent leaves the 说明 alone, null (or a
    blank string, which the service normalises to the same thing) clears it.
    """

    model_config = ConfigDict(populate_by_name=True)

    max_uses: int | None = Field(default=None, alias="maxUses")
    expires_at: int | None = Field(default=None, alias="expiresAt")
    note: str | None = Field(default=None, alias="note")


class AddSpaceManagerRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: int = Field(..., alias="userId", gt=0)
    role: str = "ADMIN"


class PatchSpaceManagerRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role: str = Field(..., min_length=1)


async def require_reviewed_space(
    request: Request,
    user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> None:
    space_id = request.path_params.get("spaceId")
    if space_id is None:
        return
    try:
        space_id = int(space_id)
    except ValueError:
        return  # Path validation supplies the normal 422 response.
    space = await SpaceRepository(db).get_by_id(space_id)
    if space is None or space.review_status == "APPROVED":
        return
    # Applications are edited through resubmission, so a review covers fixed text.
    if request.method == "GET" and request.url.path.rstrip("/").endswith(
        f"/spaces/{space_id}"
    ):
        if await SpaceReviewService(db).is_owner(space_id, user.user_id):
            return
    raise NotFoundError("Space not found")


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


async def get_space_service(db=Depends(get_db)) -> SpaceService:
    repo = SpaceRepository(session=db)
    category_repo = SpaceCategoryRepository(session=db)
    admin_repo = SpaceAdminRelationRepository(session=db)
    rank_repo = SpaceUserRankRepository(session=db)
    task_repo = TaskRepository(session=db)
    classification_topics_repo = SpaceClassificationTopicsRepository(session=db)
    domain_group_repo = SpaceDomainGroupRepository(session=db)
    domain_group_domain_repo = SpaceDomainGroupDomainRepository(session=db)
    member_repo = SpaceMemberRepository(session=db)
    invite_code_repo = SpaceInviteCodeRepository(session=db)
    return SpaceService(
        repo,
        category_repo,
        admin_repo,
        rank_repo,
        task_repo,
        classification_topics_repo=classification_topics_repo,
        domain_group_repo=domain_group_repo,
        domain_group_domain_repo=domain_group_domain_repo,
        member_repo=member_repo,
        invite_code_repo=invite_code_repo,
        # The write side of `teaching`'s references: a 项目集 may only name
        # 知识 its teacher could already read, 课件 that exist, and 课件 an
        # ordinary member could read (never one locked to a board's managers).
        knowledge_service=KnowledgeService.for_lookup(db),
        material_service=MaterialService.for_lookup(db),
        session=db,
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
        space_labels=SpaceLabels(session=db),
    )


async def _ensure_space_visible(*, db, space_id: int, user_id: int) -> None:
    """404 rather than 403 — a 题目版 you are not in is not confirmed to exist.

    Same answer the task routes give for an invisible task, and the same
    question ``list_spaces`` asks for the list query: one rule, so a direct
    link and the list cannot drift apart.
    """
    if not await SpaceRepository(db).is_member(space_id=space_id, user_id=user_id):
        raise NotFoundError(
            "Resource space not found", data={"type": "space", "id": space_id}
        )


async def _ensure_space_admin(*, db, space_id: int, user_id: int) -> None:
    """「管理员版面的门」: 只有题目板的管理员/创建者能过。

    先按 ``_ensure_space_visible`` 答 404 —— 一个你不在的题目板不该被确认存在；
    再看是不是管理员，不是就明确 403（不静默返回空内容：空 CSV 会让导出的人以为
    「这个班没人」，而真相是「你没权限」）。

    和 ``_ensure_space_visible`` 一样，判据只有一处 —— ``app.auth.space_access``
    的 ``is_space_admin``，与打分、发题、项目对话读权同一个答案。

    挂在这道门上的是一整块管理员版面：参与者花名册的导出与分组统计（逐人/分组地
    解密年级、专业、班级），以及概览、题目、发布者、提醒与其导出。前端本来就把
    整个「数据分析」入口挂在 ``isCurrentUserAtLeastAdmin`` 下面，所以这几次收窄
    是把 API 对齐到界面已经说的那句话：这版只有管理员看得到。
    学习看板（``/analytics/learning/*``）不在此列 —— 它读的是成员项目里的对话，
    由 ``app.auth.project_access`` 逐个项目判，两套数据、两个门。
    """
    await _ensure_space_visible(db=db, space_id=space_id, user_id=user_id)
    if not await is_space_admin(session=db, space_id=space_id, user_id=user_id):
        raise ForbiddenError("Only a board manager can perform this action")


def _space_to_api_model(space: Space) -> dict:
    created_at_ms = int(space.created_at.timestamp() * 1000) if space.created_at else 0
    updated_at_ms = int(space.updated_at.timestamp() * 1000) if space.updated_at else 0
    return {
        "id": space.id,
        "name": space.name,
        "intro": space.intro,
        "description": space.description,
        "reviewStatus": space.review_status,
        "reviewReason": space.review_reason,
        "avatarId": space.avatar_id,
        "enableRank": space.enable_rank,
        "visibleTaskLimit": space.visible_task_limit,
        "defaultCategoryId": space.default_category_id,
        "taskTemplates": json.dumps(space.task_templates or []),
        # 空间级「给 AI 队友的指导」(#944) — the settings form reads it back, so it
        # has to be here rather than only on the write path.
        "teaching": teaching_to_api(
            Teaching.from_json(getattr(space, "teaching", None))
        ),
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
    }


def _invite_code_to_api_model(
    invite: SpaceInviteCode, created_by: dict | None = None
) -> dict:
    """One code as the 邀请码 screen reads it.

    ``createdBy`` is the same hydrated person object a member row carries, and
    is passed in rather than read off the row because turning a user id into a
    name is a batched query the caller already runs for the whole list. It is
    ``None`` when the row names nobody — the screen says 未知 rather than
    rendering an empty name.
    """
    created_at_ms = (
        int(invite.created_at.timestamp() * 1000) if invite.created_at else 0
    )
    expires_at_ms = (
        int(invite.expires_at.timestamp() * 1000) if invite.expires_at else None
    )
    return {
        "id": invite.id,
        "spaceId": invite.space_id,
        "code": invite.code,
        "maxUses": invite.max_uses,
        "useCount": invite.use_count,
        "expiresAt": expires_at_ms,
        "createdAt": created_at_ms,
        "note": invite.note,
        "createdBy": created_by,
    }


async def _invite_codes_to_api_models(
    invites: Sequence[SpaceInviteCode],
    *,
    user_repo: UserRepository,
    profile_repo: UserProfileRepository,
) -> list[dict]:
    """The list form of the above, with its makers hydrated in one query.

    Two queries for however many codes, the same shape ``_hydrate_members``
    uses: a per-row lookup would grow with the number of codes on the board.
    """
    maker_ids = [c.created_by for c in invites if c.created_by is not None]
    people = (
        await _hydrate_people(maker_ids, user_repo=user_repo, profile_repo=profile_repo)
        if maker_ids
        else {}
    )
    return [
        _invite_code_to_api_model(
            invite, people.get(invite.created_by) if invite.created_by else None
        )
        for invite in invites
    ]


def _member_to_api_model(
    member: SpaceMember,
    user_info: dict | None = None,
    invite_code: SpaceInviteCode | None = None,
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
        # 课程级教学配置 (#8d772257) — the edit form reads it back, so it has to be
        # here rather than only on the write path.
        "teaching": teaching_to_api(Teaching.from_json(getattr(cat, "teaching", None))),
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


async def _hydrate_people(
    user_ids: Sequence[int],
    *,
    user_repo: UserRepository,
    profile_repo: UserProfileRepository,
) -> dict[int, dict]:
    """user_id → the display fields a roster row carries. Two queries for
    however many people are asked about.

    This used to be a `get_by_id` + `get_profile_by_user_id` pair per person,
    inside the loop that built the list — which made rendering a space cost
    two queries per member plus two per admin, on every read of it, with the
    count growing as the class does. Both repositories have batched readers
    for exactly this (`get_by_ids`, `get_profiles_by_user_ids`).

    Someone whose `user` row is gone falls back to the id and a placeholder
    name: the membership row is still real, and dropping them from the list
    would make the roster disagree with itself.
    """
    ids = list(user_ids)
    users = await user_repo.get_by_ids(ids)
    profiles = await profile_repo.get_profiles_by_user_ids(ids)
    # 「挑过的头像」和档案分开问，别回档案上的 avatar_id：每个注册路径都往那一列写死
    # 了全局默认（默认头像 id 因环境而异，见 UserProfileRepository.chosen_avatar_ids），
    # 直接回它会让所有没挑过头像的人共用同一张脸 —— 而区分人正是头像唯一的活。没挑过
    # 的人不在映射里，这里回 None，前端据此画彩色首字母。
    chosen_avatars = await profile_repo.chosen_avatar_ids(ids)
    people: dict[int, dict] = {}
    for user_id in ids:
        user = users.get(user_id)
        profile = profiles.get(user_id)
        people[user_id] = (
            {
                "id": user.id,
                "username": user.username,
                "nickname": profile.nickname if profile else user.username,
                "avatarId": chosen_avatars.get(user_id),
                "intro": profile.intro if profile else "",
            }
            if user
            else {"id": user_id, "username": "unknown"}
        )
    return people


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
    people = await _hydrate_people(
        [rel.user_id for rel in admin_relations],
        user_repo=user_repo,
        profile_repo=profile_repo,
    )
    return [_admin_to_api_model(rel, people[rel.user_id]) for rel in admin_relations]


async def _hydrate_members(
    members: Sequence[SpaceMember],
    *,
    user_repo: UserRepository,
    profile_repo: UserProfileRepository,
    invite_code_repo: SpaceInviteCodeRepository,
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
    codes = await invite_code_repo.get_by_ids(
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
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)

    categories = None
    if queryCategories:
        cats = await service.list_categories(space_id=space_id, include_archived=False)
        categories = [_category_to_api_model(c) for c in cats]

    my_rank = None
    viewer_id = auth_user.user_id if auth_user.user_id > 0 else None
    if queryMyRank:
        my_rank = await service.get_user_rank(space_id, viewer_id)

    # admins / classificationTopics 都由这一个构建器给齐：前端拿到任何
    # 一份 Space 响应都能直接用（它的 store 会用响应覆盖本地状态）。
    space_data = await _build_full_space_payload(space, service=service, db=db)
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
        # 和 _hydrate_people 同一判据：管理员列表也不能回档案上的原始 avatar_id，得问
        # 「这个人自己挑过没有」，没挑过就给 None 让前端画彩色首字母。
        admin_faces = await profile_repo.chosen_avatar_ids(
            [rel.user_id for rel in admin_relations]
        )
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
                    "avatarId": admin_faces.get(rel.user_id),
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

    task_templates = _expect_list(payload.task_templates, "taskTemplates")
    classification_topic_ids: list[int] = payload.classification_topics or []

    space = await service.create_space(
        name=payload.name,
        intro=payload.intro,
        description=payload.description,
        avatar_id=payload.avatar_id,
        enable_rank=payload.enable_rank,
        owner_id=auth_user.user_id,
        task_templates=task_templates,
        visible_task_limit=payload.visible_task_limit,
    )
    if classification_topic_ids:
        await service.replace_classification_topics(
            space_id=space.id,
            topic_ids=classification_topic_ids,
            actor_user_id=auth_user.user_id,
        )
    space.review_status = "PENDING"
    await db.flush()
    # 写到这里就完了，下面全是读：先提交，再构造响应。``get_db`` 的提交在
    # ``yield`` 的退出码里，而那段跑在响应发出**之后**（FastAPI 0.137 的
    # ``request_stack`` 在 ``await response(...)`` 之后才关），不在这里提交的话，
    # 客户端拿到 201 时这一行还没落地 —— 紧接着的 POST /admin/spaces/{id}/review
    # 就会 404「Space not found」（合并队列 run 36296605673 实测）。
    await db.commit()
    space_data = await _build_full_space_payload(space, service=service, db=db)
    # Every 题目版 is created holding a code (see SpaceService.create_space),
    # so hand it back here rather than making the creator come and ask.
    codes = await service.list_invite_codes(
        space_id=space.id, actor_user_id=auth_user.user_id
    )
    # Same shape the 邀请码 screen reads, its maker hydrated and all: the
    # creator is looking at this payload right after the board appears, and a
    # code that arrives without the same fields the list gives it is how two
    # views of one row drift apart.
    items = await _invite_codes_to_api_models(
        codes,
        user_repo=UserRepository(session=db),
        profile_repo=UserProfileRepository(session=db),
    )
    return {
        "code": 201,
        "message": "Created",
        "data": {
            "space": space_data,
            "inviteCode": items[0] if items else None,
        },
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


@router.post(
    "/join",
    summary="Join a Space with an invite code",
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
    return {"code": 200, "message": "OK", "data": {"space": space_data}}


@router.get(
    "/{spaceId}/members",
    summary="List Space Members",
)
async def list_space_members(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
    members = await service.list_members(space_id)
    items = await _hydrate_members(
        members,
        user_repo=UserRepository(session=db),
        profile_repo=UserProfileRepository(session=db),
        invite_code_repo=SpaceInviteCodeRepository(session=db),
    )
    return {"code": 200, "message": "OK", "data": {"members": items}}


@router.post(
    "/{spaceId}/members",
    summary="Add Space Member",
    status_code=status.HTTP_201_CREATED,
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
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
    user_repo = UserRepository(session=db)
    if await user_repo.get_by_id(payload.user_id) is None:
        raise NotFoundError(
            "User not found", data={"type": "user", "id": payload.user_id}
        )
    member = await service.add_member(
        space_id=space_id,
        target_user_id=payload.user_id,
        actor_user_id=auth_user.user_id,
    )
    items = await _hydrate_members(
        [member],
        user_repo=user_repo,
        profile_repo=UserProfileRepository(session=db),
        invite_code_repo=SpaceInviteCodeRepository(session=db),
    )
    return {"code": 201, "message": "Created", "data": {"member": items[0]}}


@router.delete(
    "/{spaceId}/members/{userId}",
    summary="Remove Space Member",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_space_member(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> Response:
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
    await service.remove_member(
        space_id=space_id,
        target_user_id=user_id,
        actor_user_id=auth_user.user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{spaceId}/leave",
    summary="Leave a Space",
    status_code=status.HTTP_204_NO_CONTENT,
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
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
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
    db=Depends(get_db),
) -> dict:
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
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
    db=Depends(get_db),
) -> Response:
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
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
