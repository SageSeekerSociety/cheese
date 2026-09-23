import json
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.auth import ActorResolverDep
from app.api.routes.tasks import get_task_membership_service
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
from app.domain.shell.catalog import is_course_shell
from app.domain.space.analytics_service import SpaceAnalyticsService
from app.domain.space.analytics_view_service import SpaceAnalyticsViewService
from app.domain.space.course_roster_service import CourseRosterService
from app.domain.space.learning_service import SpaceLearningService
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
from app.domain.space.services import SpaceService
from app.domain.space.tags_service import SpaceTagsService
from app.domain.task.models import Task
from app.domain.task.repositories import TaskMembershipRepository, TaskRepository
from app.domain.task.services import (
    TaskMembershipService,
    TaskService,
    TaskSubmissionService,
)
from app.domain.teaching.models import TeachingUnit
from app.domain.teaching.quiz_models import Quiz, QuizAnswer, QuizAttempt, QuizQuestion
from app.domain.teaching.quiz_repositories import (
    QuizAnswerRepository,
    QuizAttemptRepository,
    QuizQuestionRepository,
    QuizRepository,
)
from app.domain.teaching.quiz_services import QuizService
from app.domain.teaching.repositories import TeachingUnitRepository
from app.domain.teaching.services import TeachingUnitService
from app.domain.team.services import team_service
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

    name: str = Field(..., min_length=1, max_length=255)
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


class TeachingRequest(BaseModel):
    """课程级教学配置 (#8d772257) — 项目集级最小编辑入口。

    **The strict end of this key.** `Teaching.from_json` on the read path drops a
    bad field rather than raising, because `resolve()` runs on every turn of
    every project and a typo in one field of a 项目集 must not take down the
    twenty 赛题 under it. Here a person is looking at the form and can be told
    which field is wrong, so every field is checked and the ids are typed.
    """

    model_config = ConfigDict(populate_by_name=True)

    #: 课程级 system prompt 模板；`{current_week}` / `{allowed_topics}` /
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


class EnrollInCourseRequest(BaseModel):
    """The course link's payload: the code it carries, and nothing else.

    The link decides only WHERE the student lands, never what they may see —
    so there is no 「which parts of the course」 field here to grow.
    """

    model_config = ConfigDict(populate_by_name=True)

    code: str | None = None


class AddSpaceMemberRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: int = Field(..., alias="userId", gt=0)


class CreateSpaceInviteCodeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    max_uses: int | None = Field(default=None, alias="maxUses")
    expires_at: int | None = Field(default=None, alias="expiresAt")


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
    """「教师版面的门」: 只有题目板的管理员/创建者能过。

    先按 ``_ensure_space_visible`` 答 404 —— 一个你不在的题目板不该被确认存在；
    再看是不是管理员，不是就明确 403（不静默返回空内容：空 CSV 会让导出的人以为
    「这个班没人」，而真相是「你没权限」）。

    和 ``_ensure_space_visible`` 一样，判据只有一处 —— ``app.auth.space_access``
    的 ``is_space_admin``，与打分、发题、项目对话读权同一个答案。

    挂在这道门上的是一整块教师版面：参与者花名册的导出与分组统计（逐人/分组地
    解密年级、专业、班级），以及概览、题目、发布者、提醒与其导出。前端本来就把
    整个「数据分析」入口挂在 ``isCurrentUserAtLeastAdmin`` 下面，所以这几次收窄
    是把 API 对齐到界面已经说的那句话：这版只有教师看得到。
    学习看板（``/analytics/learning/*``）不在此列 —— 它读的是学生项目里的对话，
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
        "announcements": json.dumps(space.announcements or []),
        "taskTemplates": json.dumps(space.task_templates or []),
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
    }


def _invite_code_to_api_model(invite: SpaceInviteCode) -> dict:
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
    }


def _member_to_api_model(member: SpaceMember, user_info: dict | None = None) -> dict:
    joined_at_ms = int(member.created_at.timestamp() * 1000) if member.created_at else 0
    result: dict = {"userId": member.user_id, "joinedAt": joined_at_ms}
    if user_info is not None:
        result["user"] = user_info
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
        # 课程级教学配置 (#8d772257), `{}` when this 项目集 is not a course — the
        # edit form reads it back, so it has to be here rather than only on the
        # write path.
        "teaching": getattr(cat, "teaching", None) or {},
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
    people: dict[int, dict] = {}
    for user_id in ids:
        user = users.get(user_id)
        profile = profiles.get(user_id)
        people[user_id] = (
            {
                "id": user.id,
                "username": user.username,
                "nickname": profile.nickname if profile else user.username,
                "avatarId": profile.avatar_id if profile else None,
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
) -> list[dict]:
    people = await _hydrate_people(
        [member.user_id for member in members],
        user_repo=user_repo,
        profile_repo=profile_repo,
    )
    return [_member_to_api_model(member, people[member.user_id]) for member in members]


async def _build_course_roster_payload(
    *,
    space_id: int,
    service: SpaceService,
    db,
) -> dict:
    """这门课的人与组：结构来自 `CourseRosterService`，人味在这里补。

    拼「学生 → 他的项目 → 他的组」要人的 handle（项目记的是 `owner_handle`），
    而 handle 在 user 领域 —— 所以那一跳留在这边用现成的 `_hydrate_people` 走完，
    领域里那份服务只给平表。

    管理员不算学生：教师名单（`space.admins`）与成员表是两件事，一位老师也可以
    是成员，但他出现在「学生与分组」里只会让人数说谎。
    """
    roster = await CourseRosterService(db).roster(space_id)
    admin_ids = {rel.user_id for rel in await service.list_admins(space_id)}
    student_ids = [uid for uid in roster["memberUserIds"] if uid not in admin_ids]
    team_member_ids = [uid for team in roster["teams"] for uid in team["memberUserIds"]]
    people = await _hydrate_people(
        list(dict.fromkeys([*student_ids, *team_member_ids])),
        user_repo=UserRepository(session=db),
        profile_repo=UserProfileRepository(session=db),
    )

    projects_by_handle: dict[str, list[dict]] = {}
    for project in roster["projects"]:
        handle = project["ownerHandle"]
        if handle:
            projects_by_handle.setdefault(handle, []).append(project)

    students = []
    for user_id in student_ids:
        person = people[user_id]
        projects = projects_by_handle.get(person.get("username", ""), [])
        students.append(
            {
                "user": person,
                "projects": [
                    {"id": p["id"], "name": p["name"], "teamId": p["teamId"]}
                    for p in projects
                ],
                "teamIds": sorted(
                    {p["teamId"] for p in projects if p["teamId"] is not None}
                ),
            }
        )

    return {
        "students": students,
        "teams": [
            {
                "id": team["id"],
                "name": team["name"],
                "members": [
                    people[uid] for uid in team["memberUserIds"] if uid in people
                ],
            }
            for team in roster["teams"]
        ],
    }


async def _build_full_space_payload(
    space: Space,
    *,
    service: SpaceService,
    db,
) -> dict:
    """Build a Space response dict that matches the frontend Space type.

    Always includes `admins` (hydrated), `classificationTopics` and `isCourse` so
    that any GET/POST/PATCH response is interchangeable from the frontend's
    perspective (its store overwrites local state with the response payload).
    `isCourse` belongs here for a reason the other two do not have: the frontend
    picks the *landing* from it (course home vs. problem list) the moment a board
    is created or joined, so a response that omitted it would send a brand-new
    course to the problem list.
    """
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    space_data = _space_to_api_model(space)
    space_data["admins"] = await _build_admins_payload(
        space.id, service=service, user_repo=user_repo, profile_repo=profile_repo
    )
    topics = await service.list_classification_topics(space.id)
    space_data["classificationTopics"] = [{"id": t.id, "name": t.name} for t in topics]
    # 这块板是不是一门课：由它默认分组声明的壳算（`app.domain.shell.catalog`）。
    space_data["isCourse"] = await service.is_course(space_id=space.id)
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

    # admins / classificationTopics / isCourse 都由这一个构建器给齐：前端拿到任何
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
    pageStart: int | None = Query(default=None),
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
    # 这一页里哪些板是课程：一问拿全页，别一行一次往返（见
    # `SpaceRepository.default_category_shells`）。
    course_shells = await service.default_category_shells(space_ids=space_ids)

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
        dto["isCourse"] = is_course_shell(course_shells.get(s.id))

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
    space.review_status = "PENDING"
    await db.flush()
    space_data = await _build_full_space_payload(space, service=service, db=db)
    # Every 题目版 is created holding a code (see SpaceService.create_space),
    # so hand it back here rather than making the creator come and ask.
    codes = await service.list_invite_codes(
        space_id=space.id, actor_user_id=auth_user.user_id
    )
    return {
        "code": 201,
        "message": "Created",
        "data": {
            "space": space_data,
            "inviteCode": _invite_code_to_api_model(codes[0]) if codes else None,
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
    space_data = await _build_full_space_payload(space, service=service, db=db)
    return {"code": 200, "message": "OK", "data": {"space": space_data}}


async def _course_anchor_task(db, *, space_id: int) -> Task | None:
    """Which 题 holds a course's students' projects.

    The course keeps 一学期一个项目 by hanging every student's project on **one**
    课程题, so the anchor must be chosen by a rule that does not move just
    because the teacher published something new: the OLDEST approved, un-ended
    题 of the board's default 分组, falling back to the oldest in the board.
    Newest-first would hand every student a second project the moment a new
    assignment went up.
    """
    space = await SpaceRepository(db).get_by_id(space_id)
    default_category_id = space.default_category_id if space is not None else None
    repo = TaskRepository(session=db)
    for category_id in (default_category_id, None):
        if category_id is None and default_category_id is None:
            continue
        rows = await repo.list_tasks(
            space_id=space_id,
            category_id=category_id,
            approved=0,
            limit=10,
            sort_by="createdAt",
            sort_order="asc",
        )
        for task in rows:
            if task.ended_at is None:
                return task
    return None


async def _course_project_for(
    db, *, space_id: int, auth_user: AuthUserInfo, membership_service
):
    """The caller's project in this course, created once and reused after.

    Reuse is asked of the SPACE, not of the anchor 题 — see
    ``ProjectService.projects_in_space_for_owner``. Then the existing
    participation path does the creating, so a course project is an ordinary
    project: same protocol inheritance, same brief document, same 一学期一个项目
    key. Returns ``None`` when the course has nothing to anchor a project on
    yet, which is an honest answer rather than a stray hidden 题.
    """
    from app.domain.project.services import ProjectService

    owner = await UserRepository(session=db).get_by_id(auth_user.user_id)
    if owner is None:
        raise NotFoundError("Participant user not found")

    projects = ProjectService(db)
    existing = await projects.projects_in_space_for_owner(
        space_id=space_id, owner_handle=owner.username
    )
    if existing:
        return existing[0]

    anchor = await _course_anchor_task(db, space_id=space_id)
    if anchor is None:
        return None

    membership = await membership_service.get_membership_by_task_and_member(
        task_id=anchor.id, member_id=auth_user.user_id
    )
    if membership is None or membership.deleted_at is not None:
        membership = await membership_service.create_membership(
            task=anchor,
            member_id=auth_user.user_id,
            is_team=False,
            approved=2,  # ApproveType.NONE — the course link is not a review queue
            deadline=None,
            email=None,
            phone=None,
            apply_reason=None,
            personal_advantage=None,
            remark=None,
        )
    return await projects.for_participation(
        task=anchor, membership=membership, owner_handle=owner.username
    )


@router.post(
    "/{spaceId}/enroll",
    summary="Join a course from its link",
)
async def enroll_in_course(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    payload: EnrollInCourseRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    db=Depends(get_db),
) -> dict:
    """What the course link does, in one round trip.

    Redeems the code the link carries (the only way in for someone who is not
    a member yet), then makes sure the student has his project in this course.
    Opening the same link twice is not an error and does not mint a second
    project: membership and project are both asked for, not created blindly.
    """
    if auth_user.user_id <= 0:
        raise ForbiddenError("Only signed-in users can join a course")
    code = (payload.code or "").strip()
    if code:
        invite = await SpaceInviteCodeRepository(session=db).get_by_code(code)
        if invite is None:
            raise NotFoundError("Invite code not found", data={"type": "inviteCode"})
        if invite.space_id != space_id:
            # Answer before redeeming: a link for another board must not join
            # this person to a board the link never named.
            raise BadRequestError(
                "This invite code is for a different 题目板",
                data={"type": "inviteCode", "id": invite.id},
            )
        await service.join_space(code=code, user_id=auth_user.user_id)

    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
    project = await _course_project_for(
        db,
        space_id=space_id,
        auth_user=auth_user,
        membership_service=membership_service,
    )
    space = await service.get_space(space_id)
    if space is None:
        raise NotFoundError.for_resource("space", space_id)
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "space": {"id": space.id, "name": space.name},
            "project": (
                {
                    "id": str(project.id),
                    "name": project.name,
                    "root_topic_id": (
                        str(project.root_topic_id) if project.root_topic_id else None
                    ),
                }
                if project is not None
                else None
            ),
        },
    }


@router.get(
    "/{spaceId}/course-link",
    summary="The link a teacher hands out for this course",
)
async def get_course_link(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    """Teacher-side: one link to copy into the group chat.

    It carries an invite code, and the code is the ordinary, un-named way in —
    same as the code shown on the 邀请码 page, handed out by the same people
    (OWNER and ADMIN, see ``_ensure_space_admin``).
    """
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    codes = await service.list_invite_codes(
        space_id=space_id, actor_user_id=auth_user.user_id
    )
    now = datetime.now(UTC)
    usable = next(
        (
            c
            for c in codes
            if (c.expires_at is None or c.expires_at > now) and c.use_count < c.max_uses
        ),
        None,
    )
    if usable is None:
        usable = await service.create_invite_code(
            space_id=space_id, actor_user_id=auth_user.user_id
        )
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "path": f"/spaces/join/{usable.code}",
            "code": usable.code,
            "maxUses": usable.max_uses,
            "useCount": usable.use_count,
            "expiresAt": (
                int(usable.expires_at.timestamp() * 1000) if usable.expires_at else None
            ),
        },
    }


# ── 课程链接 (course link) ─────────────────────────────────────────────────────


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
    )
    return {"code": 200, "message": "OK", "data": {"members": items}}


@router.get(
    "/{spaceId}/course/roster",
    summary="Course Roster (teachers)",
)
async def get_course_roster(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    """这门课的人与组 —— 教师版面的「学生与分组」那一屏。

    只对本版管理员开门：它把全班的人、各自的项目与分组列在一张表上，那不是学生
    之间该互相看到的东西。判据是 ``_ensure_space_admin``（与打分、发题、读项目
    对话同一个答案），门外人先按可见性答 404，不做存在性确认。
    """
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    data = await _build_course_roster_payload(space_id=space_id, service=service, db=db)
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/course/my-group",
    summary="My Course Group (student)",
)
async def get_my_course_group(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """学生自己那一行：我在这个课里的项目，以及我挂在哪个组上。

    与花名册（``/course/roster``）分开是因为门不同：那张表把全班列在一起，只有
    教师能看；这一条问的全是关于我自己的事，所以任何能看到这块板的人都答得出。
    """
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
    viewer = await UserRepository(session=db).get_by_id(auth_user.user_id)
    data = await CourseRosterService(db).my_group(
        space_id, viewer.username if viewer else None
    )

    team = None
    team_id = data["teamId"]
    if team_id is not None:
        teams = team_service(db)
        row = await teams.get_team(team_id)
        relations = await teams.get_team_members(team_id)
        people = await _hydrate_people(
            [relation.user_id for relation in relations],
            user_repo=UserRepository(session=db),
            profile_repo=UserProfileRepository(session=db),
        )
        team = {
            "id": team_id,
            "name": row.name if row else "",
            "members": [
                people[relation.user_id]
                for relation in relations
                if relation.user_id in people
            ],
        }

    return {
        "code": 200,
        "message": "OK",
        "data": {"projectId": data["projectId"], "team": team},
    }


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
    "/{spaceId}/invite-codes",
    summary="List Space Invite Codes",
)
async def list_space_invite_codes(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> dict:
    codes = await service.list_invite_codes(
        space_id=space_id, actor_user_id=auth_user.user_id
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {"inviteCodes": [_invite_code_to_api_model(c) for c in codes]},
    }


@router.post(
    "/{spaceId}/invite-codes",
    summary="Create Space Invite Code",
    status_code=status.HTTP_201_CREATED,
)
async def create_space_invite_code(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    payload: CreateSpaceInviteCodeRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
) -> dict:
    expires_at = None
    if payload.expires_at is not None:
        expires_at = datetime.fromtimestamp(payload.expires_at / 1000.0, tz=UTC)
    invite = await service.create_invite_code(
        space_id=space_id,
        actor_user_id=auth_user.user_id,
        max_uses=payload.max_uses,
        expires_at=expires_at,
    )
    return {
        "code": 201,
        "message": "Created",
        "data": {"inviteCode": _invite_code_to_api_model(invite)},
    }


@router.get(
    "/{spaceId}/categories",
    summary="List categories in a space",
)
async def list_space_categories(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    includeArchived: bool = Query(default=False),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceService = Depends(get_space_service),
    db=Depends(get_db),
) -> dict:
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
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
    db=Depends(get_db),
) -> dict:
    """Return KPI cards, trend data, and distribution summaries for the space."""
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
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
    db=Depends(get_db),
) -> dict:
    """Return governance alert cards for the space."""
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
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
    db=Depends(get_db),
) -> dict:
    """Return publisher comparison table data."""
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
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
    db=Depends(get_db),
) -> dict:
    """Return participant population and completion analytics."""
    # 这一格把 ``_decode_identity`` 出来的年级/专业/班级做成分组统计 —— 是学生
    # 个人信息的聚合，所以和下面的导出同一个门：教师版面只有教师看。非管理员答
    # 403（不是空的分布），原因和导出一样：空的会把「你没权限」说成「这个班没人」。
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
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
    db=Depends(get_db),
) -> Response:
    """Export participant analytics as CSV (22 columns, NT-aligned).

    Writes one `UserRealNameAccessLog` row per distinct personal (non-team)
    target user to audit real-name data access, matching NT's
    `auditSpaceParticipantExport` behavior.
    """
    # 教师版面：这份 CSV 逐行写着学生的真实姓名、学号、年级、专业、班级、电话、
    # 邮箱（``_decode_identity`` 负责解密），所以只有题目板的管理员/创建者能拿。
    # 非管理员明确 403 —— 不返回空 CSV，空的会把「你没权限」误报成「这个班没人」。
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
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


# ── 学习: 学生怎么与 AI 协作、卡在哪 (issue #945 的教师看板) ────────────────────
#
# 上面那一组读 赛题 与报名表，这一组读学生项目里的**对话**，所以门也不同: 课程页
# 本身对所有人可见（`Role.GUEST` 就能读 Space），学生项目的对话不是。判定不写在
# 这几条路由里 —— 它在 `app.auth.project_access`，由 `SpaceLearningService` 逐个
# 项目过一次（`ActorResolver.authorize_project` 是同一个判据的请求内形态）。这里
# 只负责「先登录」，和本文件其它路由同一个写法。
#
# 缺了哪些数据（review_flag、「再给一点提示」、知识点）写在 `SpaceLearningService`
# 的模块说明里，接口如实把它们报成缺失，不拿别的信号顶替。


class LearningOutlineRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    block_ids: list[uuid.UUID] = Field(default_factory=list, alias="blockIds")


async def get_space_submission_service(
    db=Depends(get_db),
) -> TaskSubmissionService:
    """课程的「作业与验收」要的提交服务: 走 `routes.tasks` 那个现成的装配点。"""
    from app.api.routes.tasks import get_task_submission_service

    return await get_task_submission_service(db=db)


@router.get(
    "/{spaceId}/submissions",
    summary="Get Space Submission Queue",
)
async def get_space_submissions(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    reviewed: bool | None = Query(default=None),
    taskId: int | None = Query(default=None),
    pageStart: int | None = Query(default=None),
    pageSize: int = Query(default=20, ge=1, le=100),
    sortBy: str = Query(default="createdAt"),
    sortOrder: str = Query(default="desc"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    submission_service: TaskSubmissionService = Depends(get_space_submission_service),
    db=Depends(get_db),
) -> dict:
    """一整门课的提交与验收队列 —— 教师看的那一屏。

    按板子取一次，而不是逐道题 × 逐个学生地问（那是 N×M 次请求）。每行都带
    `taskId` / `taskTitle` / `participantId`，教师看的是「谁的哪份作业」。

    判据走那道现成的教师闸 `_ensure_space_admin`：不在这个板里答 404（不确认它
    存在），在板里但不是管理员答 403。学生看自己那一份走既有的按题接口 ——
    整门课的提交是教师版面。`reviewed=false` 就是验收队列；不给就是全部。
    """
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    if sortBy not in {"createdAt", "updatedAt"}:
        raise BadRequestError(f"Invalid sortBy: {sortBy}")
    if sortOrder not in {"asc", "desc"}:
        raise BadRequestError(f"Invalid sortOrder: {sortOrder}")

    offset = max(pageStart or 0, 0)
    items, total = await submission_service.list_for_space(
        space_id=space_id,
        task_id=taskId,
        reviewed=reviewed,
        limit=pageSize,
        offset=offset,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    returned = len(items)
    has_more = offset + returned < total
    summary = await submission_service.summary_for_space(
        space_id=space_id,
        task_id=taskId,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "submissions": items,
            "summary": summary,
            "page": {
                "pageStart": offset,
                "pageSize": returned,
                "hasMore": has_more,
                "nextStart": offset + returned if has_more and returned > 0 else None,
                "total": total,
            },
        },
    }


async def get_space_learning_service(db=Depends(get_db)) -> SpaceLearningService:
    return SpaceLearningService(session=db)


def _learning_handle(actor) -> str | None:
    """读课程对话用的是谁的 handle。

    未登录给 None —— `may_read_project` 对 None 一律回 False（"nobody asked" 不
    能读成 "anybody may"），于是页面是空的，而不是全的。
    """
    return actor.handle if actor.authenticated else None


@router.get(
    "/{spaceId}/analytics/learning/filters",
    summary="Get Space Learning Filters",
)
async def get_space_learning_filters(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    resolver: ActorResolverDep,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceLearningService = Depends(get_space_learning_service),
) -> dict:
    """这一格能筛的两维: 学生、知识点。时间那一维在前端的筛选栏里。"""
    _ = auth_user
    actor = await resolver.resolve(fallback_handle=None)
    data = await service.filters(space_id=space_id, handle=_learning_handle(actor))
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/analytics/learning/questions",
    summary="Get Space Learning Questions",
)
async def get_space_learning_questions(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    resolver: ActorResolverDep,
    student: str | None = Query(default=None),
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    knowledgePoint: int | None = Query(default=None),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceLearningService = Depends(get_space_learning_service),
) -> dict:
    """按学生 / 时间 / 知识点筛出来的学生发言，每条都带得回原文的坐标。"""
    _ = auth_user
    actor = await resolver.resolve(fallback_handle=None)
    data = await service.questions(
        space_id=space_id,
        handle=_learning_handle(actor),
        student=student,
        from_ts=from_ts,
        to_ts=to_ts,
        knowledge_point=knowledgePoint,
    )
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/analytics/learning/queues",
    summary="Get Space Learning Queues",
)
async def get_space_learning_queues(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    resolver: ActorResolverDep,
    student: str | None = Query(default=None),
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceLearningService = Depends(get_space_learning_service),
) -> dict:
    """共性问题两条来源，各自一个队列。"""
    _ = auth_user
    actor = await resolver.resolve(fallback_handle=None)
    data = await service.queues(
        space_id=space_id,
        handle=_learning_handle(actor),
        student=student,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    return {"code": 200, "message": "OK", "data": data}


@router.post(
    "/{spaceId}/analytics/learning/outline",
    summary="Build Space Learning Outline",
)
async def build_space_learning_outline(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    body: LearningOutlineRequest,
    resolver: ActorResolverDep,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceLearningService = Depends(get_space_learning_service),
) -> dict:
    """把勾中的几条拼成一份能直接上课用的讲解提纲。

    POST 而不是 GET: 勾的是哪几条会随人一直变，而且可能几十个 id —— 放进查询串
    会撞上长度上限，也会在访问日志里留下别人的引用。
    """
    _ = auth_user
    actor = await resolver.resolve(fallback_handle=None)
    data = await service.outline(
        space_id=space_id,
        handle=_learning_handle(actor),
        block_ids=body.block_ids,
    )
    return {"code": 200, "message": "OK", "data": data}


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
    db=Depends(get_db),
) -> Response:
    """Export task analytics as CSV (16 columns, NT-aligned)."""
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
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
    db=Depends(get_db),
) -> Response:
    """Export publisher analytics as CSV (11 columns, NT-aligned)."""
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
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
    db=Depends(get_db),
) -> dict:
    """Return the authenticated user's publishing summary in this space."""
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
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
    db=Depends(get_db),
) -> dict:
    """Return the authenticated user's published tasks in this space."""
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
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
    db=Depends(get_db),
) -> dict:
    """Return the authenticated user's participation summary in this space."""
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
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
    db=Depends(get_db),
) -> dict:
    """Return the authenticated user's participation list in this space."""
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
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


# ── 教学单元（一门课的时间线） ─────────────────────────────────────────────────
#
# 读的一次给所有人，写的一次给教师：学生只看得到发布过的，而「发布过」这个判断在
# ``TeachingUnitService`` 的查询里，不在这里的分支里 —— 前端过滤过不了这一关，
# 将来 agent 注入也复用同一条查询。


class CreateTeachingUnitRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    week: int
    title: str = Field(..., min_length=1, max_length=255)
    summary: str = ""
    knowledge_point_ids: list[int] = Field(
        default_factory=list, alias="knowledgePointIds"
    )
    material_ids: list[int] = Field(default_factory=list, alias="materialIds")
    assignment_task_id: int | None = Field(default=None, alias="assignmentTaskId")
    due_at: datetime | None = Field(default=None, alias="dueAt")
    published: bool = False


class PatchTeachingUnitRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    week: int | None = None
    title: str | None = None
    summary: str | None = None
    knowledge_point_ids: list[int] | None = Field(
        default=None, alias="knowledgePointIds"
    )
    material_ids: list[int] | None = Field(default=None, alias="materialIds")
    assignment_task_id: int | None = Field(default=None, alias="assignmentTaskId")
    clear_assignment: bool = Field(default=False, alias="clearAssignment")
    due_at: datetime | None = Field(default=None, alias="dueAt")
    clear_due_at: bool = Field(default=False, alias="clearDueAt")
    published: bool | None = None


class CreateQuizRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., min_length=1, max_length=255)
    due_at: datetime | None = Field(default=None, alias="dueAt")


class PatchQuizRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str | None = None
    due_at: datetime | None = Field(default=None, alias="dueAt")
    clear_due_at: bool = Field(default=False, alias="clearDueAt")


class CreateQuizQuestionRequest(BaseModel):
    """一道题。

    ``answer`` 的形状由 ``kind`` 决定（下标 / 下标表 / 字符串 / 参考文本），**这里
    不收窄**：形状检查一处做完才看得出来错（见 ``quiz_services._check_question``）。
    """

    model_config = ConfigDict(populate_by_name=True)

    kind: str
    prompt: str = Field(..., min_length=1)
    options: list[str] = Field(default_factory=list)
    answer: Any = None
    points: int = 0


class PatchQuizQuestionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: str | None = None
    prompt: str | None = None
    options: list[str] | None = None
    answer: Any = None
    points: int | None = None


class QuizAnswerIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    question_id: int = Field(..., alias="questionId")
    response: Any = None


class SubmitQuizAttemptRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    answers: list[QuizAnswerIn] = Field(default_factory=list)


class GradeQuizAnswerRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    points: int
    comment: str = ""


async def get_teaching_unit_service(
    db=Depends(get_db),
) -> TeachingUnitService:
    return TeachingUnitService(
        repo=TeachingUnitRepository(db), task_service=TaskService(TaskRepository(db))
    )


async def get_quiz_service(
    db=Depends(get_db),
) -> QuizService:
    return QuizService(
        quizzes=QuizRepository(db),
        questions=QuizQuestionRepository(db),
        attempts=QuizAttemptRepository(db),
        answers=QuizAnswerRepository(db),
        units=TeachingUnitRepository(db),
    )


def _quiz_to_api_model(quiz: Quiz) -> dict:
    return {
        "id": quiz.id,
        "spaceId": quiz.space_id,
        "unitId": quiz.unit_id,
        "title": quiz.title,
        "dueAt": _ts(quiz.due_at),
    }


def _quiz_question_to_api_model(
    question: QuizQuestion, *, include_answer: bool
) -> dict:
    """``include_answer`` 只对教师为真 —— **答案键从不发给学生**（见模块说明）。"""
    out = {
        "id": question.id,
        "position": question.position,
        "kind": question.kind,
        "prompt": question.prompt,
        "options": list(question.options or []),
        "points": question.points,
    }
    if include_answer:
        out["answer"] = question.answer
    return out


def _quiz_attempt_to_api_model(attempt: QuizAttempt) -> dict:
    return {
        "id": attempt.id,
        "userId": attempt.user_id,
        "submittedAt": _ts(attempt.submitted_at),
        "gradedAt": _ts(attempt.graded_at),
    }


def _quiz_answer_to_api_model(answer: QuizAnswer) -> dict:
    return {
        "questionId": answer.question_id,
        "response": answer.response,
        "awardedPoints": answer.awarded_points,
        "comment": answer.comment,
        "needsReview": answer.awarded_points is None,
    }


def _quiz_payload_to_api_model(payload: dict) -> dict:
    """一条小测页要的全部东西，按看的人分两种形状。

    学生那份**没有答案键**，只有自己那一次作答与每题得分；教师那份有答案键，并多
    一个「判完了没有」。分叉放在这里一次做完，别让每条路由各判一次。
    """
    quiz = payload.get("quiz")
    if quiz is None:
        return {"quiz": None, "unitId": payload["unitId"]}
    can_teach = bool(payload.get("canTeach"))
    questions = [
        _quiz_question_to_api_model(question, include_answer=can_teach)
        for question in payload["questions"]
    ]
    my_answers = payload.get("myAnswers", [])
    score = sum(answer["awardedPoints"] or 0 for answer in my_answers)
    attempt = payload.get("myAttempt")
    my_attempt = _quiz_attempt_to_api_model(attempt) if attempt is not None else None
    if my_attempt is not None and attempt is not None:
        my_attempt["pendingReview"] = attempt.graded_at is None
        my_attempt["score"] = score
    out = {
        "quiz": _quiz_to_api_model(quiz),
        "canTeach": can_teach,
        "questions": questions,
        "maxScore": payload["maxScore"],
        "myAttempt": my_attempt,
        "myAnswers": my_answers,
    }
    if can_teach:
        out["submissions"] = payload.get("submissions", [])
        out["reviewQueue"] = payload.get("reviewQueue", [])
    return out


def _ts(value: datetime | None) -> int | None:
    """毫秒时间戳，或者 None —— 前端拿到的一律是这个（别在每处各写一遍）。"""
    return int(value.timestamp() * 1000) if value else None


def _teaching_unit_to_api_model(
    unit: TeachingUnit, *, quiz_id: int | None = None
) -> dict:
    return {
        "id": unit.id,
        "spaceId": unit.space_id,
        "week": unit.week,
        "title": unit.title,
        "summary": unit.summary,
        "knowledgePointIds": list(unit.knowledge_point_ids or []),
        "materialIds": list(unit.material_ids or []),
        "assignmentTaskId": unit.assignment_task_id,
        "publishedAt": _ts(unit.published_at),
        "dueAt": _ts(unit.due_at),
        # 这一周有没有小测（NULL = 没有）。学生首页靠它决定要不要给「本周有小测」
        # 那个入口，所以它跟着单元列表一起下来，而不是让前端逐周去问一次。
        "quizId": quiz_id,
    }


@router.get(
    "/{spaceId}/units",
    summary="List Teaching Units",
)
async def list_space_units(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: TeachingUnitService = Depends(get_teaching_unit_service),
    quiz_service: QuizService = Depends(get_quiz_service),
    db=Depends(get_db),
) -> dict:
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
    can_teach = await is_space_admin(
        session=db, space_id=space_id, user_id=auth_user.user_id
    )
    units = await service.list_units(space_id=space_id, published_only=not can_teach)
    quiz_ids = await quiz_service.quiz_ids_by_unit(unit_ids=[unit.id for unit in units])
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "units": [
                _teaching_unit_to_api_model(unit, quiz_id=quiz_ids.get(unit.id))
                for unit in units
            ],
            "canTeach": can_teach,
        },
    }


@router.post(
    "/{spaceId}/units",
    summary="Create Teaching Unit",
    status_code=status.HTTP_201_CREATED,
)
async def create_space_unit(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    payload: CreateTeachingUnitRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: TeachingUnitService = Depends(get_teaching_unit_service),
    db=Depends(get_db),
) -> dict:
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    unit = await service.create_unit(
        space_id=space_id,
        actor_id=auth_user.user_id,
        week=payload.week,
        title=payload.title,
        summary=payload.summary,
        knowledge_point_ids=payload.knowledge_point_ids,
        material_ids=payload.material_ids,
        assignment_task_id=payload.assignment_task_id,
        published=payload.published,
        due_at=payload.due_at,
    )
    return {
        "code": 201,
        "message": "Created",
        "data": {"unit": _teaching_unit_to_api_model(unit)},
    }


@router.patch(
    "/{spaceId}/units/{unitId}",
    summary="Update Teaching Unit",
)
async def patch_space_unit(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    unit_id: Annotated[int, Path(ge=1, alias="unitId")],
    payload: PatchTeachingUnitRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: TeachingUnitService = Depends(get_teaching_unit_service),
    quiz_service: QuizService = Depends(get_quiz_service),
    db=Depends(get_db),
) -> dict:
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    unit = await service.update_unit(
        space_id=space_id,
        unit_id=unit_id,
        week=payload.week,
        title=payload.title,
        summary=payload.summary,
        knowledge_point_ids=payload.knowledge_point_ids,
        material_ids=payload.material_ids,
        assignment_task_id=payload.assignment_task_id,
        clear_assignment=payload.clear_assignment,
        published=payload.published,
        due_at=payload.due_at,
        clear_due_at=payload.clear_due_at,
    )
    quiz_ids = await quiz_service.quiz_ids_by_unit(unit_ids=[unit.id])
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "unit": _teaching_unit_to_api_model(unit, quiz_id=quiz_ids.get(unit.id))
        },
    }


@router.delete(
    "/{spaceId}/units/{unitId}",
    summary="Delete Teaching Unit",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_space_unit(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    unit_id: Annotated[int, Path(ge=1, alias="unitId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: TeachingUnitService = Depends(get_teaching_unit_service),
    db=Depends(get_db),
) -> Response:
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    await service.delete_unit(space_id=space_id, unit_id=unit_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# 小测（一门课里的一周）
#
# 可见性只有一条：**单元发布了，这一周的小测才存在**（服务里那一处
# ``_require_published``），所以这里没有第二个发布开关。写的一次只走
# ``_ensure_space_admin``（本版管理员 = 这门课的老师），读的一次先过可见性。
# ---------------------------------------------------------------------------


async def _attach_people_to(rows: list[dict], *, db) -> None:
    """给「谁交的」那一列补上显示名 —— 一次问两个查询，别按人循环。"""
    user_ids = sorted({row["userId"] for row in rows})
    if not user_ids:
        return
    people = await _hydrate_people(
        user_ids,
        user_repo=UserRepository(db),
        profile_repo=UserProfileRepository(db),
    )
    for row in rows:
        row["user"] = people.get(row["userId"])


@router.get(
    "/{spaceId}/units/{unitId}/quiz",
    summary="Get the quiz of a week",
)
async def get_unit_quiz(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    unit_id: Annotated[int, Path(ge=1, alias="unitId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuizService = Depends(get_quiz_service),
    db=Depends(get_db),
) -> dict:
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
    can_teach = await is_space_admin(
        session=db, space_id=space_id, user_id=auth_user.user_id
    )
    payload = await service.quiz_for_unit(
        space_id=space_id,
        unit_id=unit_id,
        viewer_id=auth_user.user_id,
        can_teach=can_teach,
    )
    if can_teach:
        await _attach_people_to(
            payload.get("submissions", []) + payload.get("reviewQueue", []), db=db
        )
    return {
        "code": 200,
        "message": "OK",
        "data": _quiz_payload_to_api_model(payload),
    }


@router.get(
    "/{spaceId}/quizzes/{quizId}",
    summary="Get A Quiz",
)
async def get_space_quiz(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    quiz_id: Annotated[int, Path(ge=1, alias="quizId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuizService = Depends(get_quiz_service),
    db=Depends(get_db),
) -> dict:
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
    can_teach = await is_space_admin(
        session=db, space_id=space_id, user_id=auth_user.user_id
    )
    payload = await service.quiz_by_id(
        space_id=space_id,
        quiz_id=quiz_id,
        viewer_id=auth_user.user_id,
        can_teach=can_teach,
    )
    if can_teach:
        await _attach_people_to(
            payload.get("submissions", []) + payload.get("reviewQueue", []), db=db
        )
    return {
        "code": 200,
        "message": "OK",
        "data": _quiz_payload_to_api_model(payload),
    }


@router.post(
    "/{spaceId}/units/{unitId}/quiz",
    summary="Create The Week's Quiz",
    status_code=status.HTTP_201_CREATED,
)
async def create_unit_quiz(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    unit_id: Annotated[int, Path(ge=1, alias="unitId")],
    payload: CreateQuizRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuizService = Depends(get_quiz_service),
    db=Depends(get_db),
) -> dict:
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    quiz = await service.create_quiz(
        space_id=space_id,
        unit_id=unit_id,
        actor_id=auth_user.user_id,
        title=payload.title,
        due_at=payload.due_at,
    )
    return {
        "code": 201,
        "message": "Created",
        "data": {"quiz": _quiz_to_api_model(quiz)},
    }


@router.patch(
    "/{spaceId}/quizzes/{quizId}",
    summary="Update A Quiz",
)
async def patch_space_quiz(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    quiz_id: Annotated[int, Path(ge=1, alias="quizId")],
    payload: PatchQuizRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuizService = Depends(get_quiz_service),
    db=Depends(get_db),
) -> dict:
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    quiz = await service.update_quiz(
        space_id=space_id,
        quiz_id=quiz_id,
        title=payload.title,
        due_at=payload.due_at,
        clear_due_at=payload.clear_due_at,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {"quiz": _quiz_to_api_model(quiz)},
    }


@router.delete(
    "/{spaceId}/quizzes/{quizId}",
    summary="Delete A Quiz",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_space_quiz(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    quiz_id: Annotated[int, Path(ge=1, alias="quizId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuizService = Depends(get_quiz_service),
    db=Depends(get_db),
) -> Response:
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    await service.delete_quiz(space_id=space_id, quiz_id=quiz_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{spaceId}/quizzes/{quizId}/questions",
    summary="Add A Quiz Question",
    status_code=status.HTTP_201_CREATED,
)
async def add_quiz_question(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    quiz_id: Annotated[int, Path(ge=1, alias="quizId")],
    payload: CreateQuizQuestionRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuizService = Depends(get_quiz_service),
    db=Depends(get_db),
) -> dict:
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    question = await service.add_question(
        space_id=space_id,
        quiz_id=quiz_id,
        kind=payload.kind,
        prompt=payload.prompt,
        options=payload.options,
        answer=payload.answer,
        points=payload.points,
    )
    return {
        "code": 201,
        "message": "Created",
        "data": {
            "question": _quiz_question_to_api_model(question, include_answer=True)
        },
    }


@router.patch(
    "/{spaceId}/quizzes/{quizId}/questions/{questionId}",
    summary="Update A Quiz Question",
)
async def patch_quiz_question(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    quiz_id: Annotated[int, Path(ge=1, alias="quizId")],
    question_id: Annotated[int, Path(ge=1, alias="questionId")],
    payload: PatchQuizQuestionRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuizService = Depends(get_quiz_service),
    db=Depends(get_db),
) -> dict:
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    question = await service.update_question(
        space_id=space_id,
        quiz_id=quiz_id,
        question_id=question_id,
        kind=payload.kind,
        prompt=payload.prompt,
        options=payload.options,
        answer=payload.answer,
        points=payload.points,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "question": _quiz_question_to_api_model(question, include_answer=True)
        },
    }


@router.delete(
    "/{spaceId}/quizzes/{quizId}/questions/{questionId}",
    summary="Delete A Quiz Question",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_quiz_question(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    quiz_id: Annotated[int, Path(ge=1, alias="quizId")],
    question_id: Annotated[int, Path(ge=1, alias="questionId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuizService = Depends(get_quiz_service),
    db=Depends(get_db),
) -> Response:
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    await service.delete_question(
        space_id=space_id, quiz_id=quiz_id, question_id=question_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/{spaceId}/quizzes/{quizId}/my-attempt",
    summary="Answer A Quiz",
)
async def submit_quiz_attempt(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    quiz_id: Annotated[int, Path(ge=1, alias="quizId")],
    payload: SubmitQuizAttemptRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuizService = Depends(get_quiz_service),
    db=Depends(get_db),
) -> dict:
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
    answers = {item.question_id: item.response for item in payload.answers}
    result = await service.submit(
        space_id=space_id,
        quiz_id=quiz_id,
        user_id=auth_user.user_id,
        answers=answers,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": _quiz_payload_to_api_model(result),
    }


@router.patch(
    "/{spaceId}/quizzes/{quizId}/answers/{answerId}",
    summary="Grade A Quiz Answer",
)
async def grade_quiz_answer(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    quiz_id: Annotated[int, Path(ge=1, alias="quizId")],
    answer_id: Annotated[int, Path(ge=1, alias="answerId")],
    payload: GradeQuizAnswerRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuizService = Depends(get_quiz_service),
    db=Depends(get_db),
) -> dict:
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    answer = await service.grade_answer(
        space_id=space_id,
        quiz_id=quiz_id,
        answer_id=answer_id,
        actor_id=auth_user.user_id,
        points=payload.points,
        comment=payload.comment,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {"answer": _quiz_answer_to_api_model(answer)},
    }
