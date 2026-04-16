from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.auth.checker import get_auth_user, require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    QuotaExceededError,
)
from app.db.session import get_db
from app.domain.llm.repositories import AIUserQuotaRepository
from app.domain.llm.services import AiAdviceService
from app.domain.space.rank_service import SpaceRankService
from app.domain.space.repositories import (
    SpaceAdminRelationRepository,
    SpaceCategoryRepository,
    SpaceRepository,
    SpaceUserRankRepository,
)
from app.domain.task.models import Task, TaskMembership, TaskTopicsRelation
from app.domain.task.repositories import (
    AIConversationRepository,
    AIMessageRepository,
    TaskAIAdviceContextRepository,
    TaskAIAdviceRepository,
    TaskMembershipRepository,
    TaskRepository,
    TaskSubmissionEntryRepository,
    TaskSubmissionRepository,
    TaskSubmissionReviewRepository,
    TaskSubmissionSchemaRepository,
    TopicRepository,
)
from app.domain.task.services import (
    TaskMembershipService,
    TaskService,
    TaskSubmissionReviewService,
    TaskSubmissionService,
)
from app.domain.task.task_ai_advice_service import TaskAIAdviceService
from app.domain.team.repositories import TeamRepository
from app.domain.team.services import TeamService
from app.domain.topics.repositories import TopicRepository as GlobalTopicRepository
from app.domain.user.repositories import UserRealNameRepository
from app.domain.user.repositories import UserProfileRepository, UserRepository

router = APIRouter(prefix="/tasks", tags=["Tasks"])


async def get_task_service(db=Depends(get_db)) -> TaskService:
    repo = TaskRepository(session=db)
    return TaskService(repo)


async def get_task_membership_service(db=Depends(get_db)) -> TaskMembershipService:
    repo = TaskMembershipRepository(session=db)
    realname_repo = UserRealNameRepository(session=db)
    space_repo = SpaceRepository(session=db)
    space_rank_repo = SpaceUserRankRepository(session=db)
    return TaskMembershipService(
        repo=repo,
        realname_repo=realname_repo,
        space_repo=space_repo,
        space_rank_repo=space_rank_repo,
    )


async def get_task_submission_service(db=Depends(get_db)) -> TaskSubmissionService:
    submission_repo = TaskSubmissionRepository(session=db)
    entry_repo = TaskSubmissionEntryRepository(session=db)
    review_repo = TaskSubmissionReviewRepository(session=db)
    membership_repo = TaskMembershipRepository(session=db)
    return TaskSubmissionService(
        submission_repo=submission_repo,
        entry_repo=entry_repo,
        review_repo=review_repo,
        membership_repo=membership_repo,
    )


async def get_task_submission_review_service(
    db=Depends(get_db),
) -> TaskSubmissionReviewService:
    review_repo = TaskSubmissionReviewRepository(session=db)
    submission_repo = TaskSubmissionRepository(session=db)
    membership_repo = TaskMembershipRepository(session=db)
    task_repo = TaskRepository(session=db)
    space_repo = SpaceRepository(session=db)
    rank_repo = SpaceUserRankRepository(session=db)
    rank_service = SpaceRankService(space_repo=space_repo, rank_repo=rank_repo)
    return TaskSubmissionReviewService(
        review_repo=review_repo,
        submission_repo=submission_repo,
        membership_repo=membership_repo,
        task_repo=task_repo,
        rank_service=rank_service,
    )


async def get_team_service(db=Depends(get_db)) -> TeamService:
    repo = TeamRepository(session=db)
    return TeamService(repo)


async def get_task_ai_advice_service(db=Depends(get_db)) -> TaskAIAdviceService:
    advice_repo = TaskAIAdviceRepository(session=db)
    conversation_repo = AIConversationRepository(session=db)
    message_repo = AIMessageRepository(session=db)
    context_repo = TaskAIAdviceContextRepository(session=db)
    task_repo = TaskRepository(session=db)
    quota_repo = AIUserQuotaRepository(session=db)
    quota_service = AiAdviceService(repo=quota_repo)
    return TaskAIAdviceService(
        advice_repo=advice_repo,
        conversation_repo=conversation_repo,
        message_repo=message_repo,
        context_repo=context_repo,
        task_repo=task_repo,
        quota_service=quota_service,
    )


class TaskAIAdviceConversationContext(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    section: str | None = None
    section_index: int | None = Field(default=None, alias="sectionIndex")
    index: int | None = None


class CreateTaskAIAdviceConversationRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    question: str
    parent_id: int | None = Field(default=None, alias="parentId")
    conversation_id: str | None = Field(default=None, alias="conversationId")
    model_type: str | None = Field(default=None, alias="modelType")
    context: TaskAIAdviceConversationContext | None = None

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        return value.strip()


def _task_to_api_model(task: Task) -> dict:
    created_at_ms = int(task.created_at.timestamp() * 1000) if task.created_at is not None else 0
    updated_at_ms = int(task.updated_at.timestamp() * 1000) if task.updated_at is not None else 0
    deadline_ms = int(task.deadline.timestamp() * 1000) if task.deadline is not None else None
    registration_start_ms = (
        int(task.registration_start_at.timestamp() * 1000)
        if task.registration_start_at is not None
        else None
    )
    approved_map = {0: "APPROVED", 1: "DISAPPROVED", 2: "NONE"}
    submitter_type_map = {0: "USER", 1: "TEAM"}
    return {
        "id": task.id,
        "name": task.name,
        "intro": task.intro,
        "description": task.description,
        "deadline": deadline_ms,
        "registrationStartAt": registration_start_ms,
        "defaultDeadline": task.default_deadline,
        "resubmittable": task.resubmittable,
        "editable": task.editable,
        "approved": approved_map.get(task.approved, "NONE"),
        "rank": task.rank,
        "submitterType": submitter_type_map.get(task.submitter_type, "USER"),
        "submissionSchema": [],
        "space": {"id": task.space_id},
        "category": {"id": task.category_id, "name": ""},
        "categoryId": task.category_id,
        "createdBy": task.creator_id,
        "creator": {"id": task.creator_id},
        "requireRealName": task.require_real_name,
        "participantLimit": task.participant_limit,
        "minTeamSize": task.min_team_size,
        "maxTeamSize": task.max_team_size,
        "teamLockingPolicy": task.team_locking_policy,
        "rejectReason": task.reject_reason,
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
    }


async def _enrich_task_models(
    db,
    task_models: list[dict],
    *,
    space_id: int,
) -> list[dict]:
    if not task_models:
        return task_models

    task_ids = [task_model["id"] for task_model in task_models]
    creator_ids = [task_model.get("creator", {}).get("id") for task_model in task_models]
    creator_ids = [creator_id for creator_id in creator_ids if isinstance(creator_id, int)]

    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    membership_repo = TaskMembershipRepository(session=db)
    category_repo = SpaceCategoryRepository(session=db)
    admin_repo = SpaceAdminRelationRepository(session=db)
    space_repo = SpaceRepository(session=db)

    admin_relations = await admin_repo.list_admins(space_id)
    admin_user_ids = [rel.user_id for rel in admin_relations]
    all_user_ids = list({*creator_ids, *admin_user_ids})

    users = await user_repo.get_by_ids(all_user_ids)
    profiles = await profile_repo.get_profiles_by_user_ids(all_user_ids)
    memberships = await membership_repo.list_memberships_for_space(space_id)
    categories = await category_repo.list_categories_for_space(space_id, include_archived=True)
    space = await space_repo.get_by_id(space_id)
    space_name = space.name if space is not None else "Unknown Space"

    role_name_map = {0: "OWNER", 1: "ADMIN"}
    admins_payload: list[dict] = []
    for rel in admin_relations:
        user = users.get(rel.user_id)
        profile = profiles.get(rel.user_id)
        if user is not None:
            nickname = profile.nickname if profile and profile.nickname else user.username
            avatar_id = profile.avatar_id if profile else None
            intro = profile.intro if profile else ""
            user_payload = {
                "id": user.id,
                "username": user.username,
                "nickname": nickname,
                "avatarId": avatar_id,
                "intro": intro,
                "follow_count": 0,
                "fans_count": 0,
                "question_count": 0,
                "answer_count": 0,
            }
        else:
            user_payload = {
                "id": rel.user_id,
                "username": "unknown",
                "nickname": "unknown",
                "avatarId": None,
                "intro": "",
                "follow_count": 0,
                "fans_count": 0,
                "question_count": 0,
                "answer_count": 0,
            }

        admins_payload.append(
            {
                "role": role_name_map.get(rel.role, "ADMIN"),
                "user": user_payload,
            }
        )

    category_name_map: dict[int, str] = {
        int(category.id): category.name for category in categories if getattr(category, "name", None)
    }

    participant_counts: dict[int, int] = {task_id: 0 for task_id in task_ids}
    for membership in memberships:
        if membership.task_id in participant_counts:
            participant_counts[membership.task_id] += 1

    for task_model in task_models:
        creator_id = task_model.get("creator", {}).get("id")
        category_id = task_model.get("categoryId")
        if not isinstance(category_id, int):
            category_id = task_model.get("category", {}).get("id")
        user = users.get(creator_id) if isinstance(creator_id, int) else None
        profile = profiles.get(creator_id) if isinstance(creator_id, int) else None

        if user is not None:
            nickname = profile.nickname if profile and profile.nickname else user.username
            avatar_id = profile.avatar_id if profile else None
            intro = profile.intro if profile else ""
            task_model["creator"] = {
                "id": user.id,
                "username": user.username,
                "nickname": nickname,
                "avatarId": avatar_id,
                "intro": intro,
            }
        else:
            task_model["creator"] = {
                "id": creator_id,
                "username": "unknown",
                "nickname": "unknown",
                "avatarId": None,
                "intro": "",
            }

        task_model["participants"] = {
            "total": participant_counts.get(task_model["id"], 0),
            "examples": [],
        }

        current_space_id = task_model.get("space", {}).get("id")
        if not isinstance(current_space_id, int):
            current_space_id = space_id
        task_model["space"] = {
            "id": current_space_id,
            "name": space_name,
            "admins": admins_payload,
        }

        resolved_category_name = (
            category_name_map.get(category_id)
            if isinstance(category_id, int)
            else None
        )
        task_model["category"] = {
            "id": category_id,
            "name": resolved_category_name or "Uncategorized",
        }
        task_model["categoryId"] = category_id

    return task_models


def _membership_to_api_model(
    membership: TaskMembership,
    *,
    participant_info: dict | None = None,
) -> dict:
    """Minimal TaskMembership representation for participants list.

    NOTE: This is a simplified view that focuses on structure. More fields
    (real name info, team members, etc.) can be added as needed.
    """
    created_at_ms = (
        int(membership.created_at.timestamp() * 1000) if membership.created_at is not None else 0
    )
    updated_at_ms = (
        int(membership.updated_at.timestamp() * 1000) if membership.updated_at is not None else 0
    )

    participant = participant_info or {"id": membership.member_id}
    member = {"id": membership.member_id}

    approved_map = {0: "APPROVED", 1: "DISAPPROVED", 2: "NONE"}
    approved_str = approved_map.get(membership.approved, "NONE")

    return {
        "id": membership.id,
        "taskId": membership.task_id,
        "memberId": membership.member_id,
        "member": member,
        "participant": participant,
        "isTeam": membership.is_team,
        "email": membership.email,
        "phone": membership.phone,
        "completionStatus": membership.completion_status,
        "approved": approved_str,
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
    }


def _map_submitter_type(value: str) -> int:
    """Map TaskSubmitterTypeDTO string to smallint ordinal."""
    mapping = {
        "USER": 0,
        "TEAM": 1,
    }
    if value not in mapping:
        raise BadRequestError(f"Invalid submitterType: {value}")
    return mapping[value]


def _map_approve_type(value: str) -> int:
    """Map ApproveTypeDTO string to smallint ordinal."""
    mapping = {
        "APPROVED": 0,
        "DISAPPROVED": 1,
        "NONE": 2,
    }
    upper = value.upper()
    if upper not in mapping:
        raise BadRequestError(f"Invalid approved value: {value}")
    return mapping[upper]


async def _validate_and_get_category_id(
    *,
    space_repo: SpaceRepository,
    category_repo: SpaceCategoryRepository,
    space_id: int,
    category_id: int | None,
) -> int:
    """Validate category for a space, mirroring Kotlin validateAndGetCategory."""

    async def _get_space_default_category_id() -> int:
        space = await space_repo.get_by_id(space_id)
        if space is None:
            raise NotFoundError("Space not found")
        if space.default_category_id is None:
            raise BadRequestError("Space has no default category configured.")
        return space.default_category_id

    async def _load_and_validate_category(cid: int) -> int:
        category = await category_repo.get_by_id_and_space(cid, space_id)
        if category is None:
            raise NotFoundError("Category not found or does not belong to space.")
        if getattr(category, "archived_at", None) is not None:
            raise BadRequestError(f"Cannot assign task to an archived category (id={cid}).")
        if category.deleted_at is not None:
            raise BadRequestError(f"Cannot assign task to a deleted category (id={cid}).")
        return category.id

    # When category_id is explicitly provided, validate it.
    if category_id is not None:
        return await _load_and_validate_category(category_id)

    # Otherwise, fall back to space.default_category_id.
    default_cid = await _get_space_default_category_id()
    return await _load_and_validate_category(default_cid)


def _map_approve_type_to_int(value: str | None) -> int | None:
    if value is None:
        return None
    mapping = {
        "APPROVED": 0,
        "DISAPPROVED": 1,
        "NONE": 2,
    }
    upper = value.upper()
    if upper not in mapping:
        raise BadRequestError(f"Invalid approved value: {value}")
    return mapping[upper]


@router.post(
    "",
    summary="Create Task",
)
async def create_task(
    payload: dict,
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    """Create a new task (simplified port of Kotlin TaskService.createTask).

    NOTE:
    - 当前版本不检查复杂权限，只要求提供 space 并验证 category 归属；
    - submissionSchema / topics 仅做占位处理，暂不影响提交与评分。
    """
    required_fields = [
        "name",
        "submitterType",
        "resubmittable",
        "editable",
        "intro",
        "description",
        "space",
    ]
    for field in required_fields:
        if field not in payload:
            raise BadRequestError(f"Missing required field: {field}")

    try:
        name = str(payload["name"])
        submitter_type_raw = str(payload["submitterType"])
        submitter_type = _map_submitter_type(submitter_type_raw)
        resubmittable = bool(payload["resubmittable"])
        editable = bool(payload["editable"])
        intro = str(payload["intro"])
        description = str(payload["description"])
        space_id = int(payload["space"])
    except (TypeError, ValueError) as exc:
        raise BadRequestError(f"Invalid field types: {exc}") from exc

    participant_limit_raw = payload.get("participantLimit")
    participant_limit: int | None
    if participant_limit_raw is None:
        participant_limit = None
    else:
        try:
            participant_limit = int(participant_limit_raw)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid participantLimit: {exc}") from exc

    default_deadline_raw = payload.get("defaultDeadline", 30)
    try:
        default_deadline = int(default_deadline_raw)
    except (TypeError, ValueError) as exc:
        raise BadRequestError(f"Invalid defaultDeadline: {exc}") from exc

    deadline_ms = payload.get("deadline")
    deadline_dt: datetime | None = None
    if deadline_ms is not None:
        try:
            deadline_dt = datetime.fromtimestamp(int(deadline_ms) / 1000.0, tz=UTC).replace(
                tzinfo=None
            )
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid deadline: {exc}") from exc

    registration_start_ms = payload.get("registrationStartAt")
    registration_start_dt: datetime | None = None
    if registration_start_ms is not None:
        try:
            registration_start_dt = datetime.fromtimestamp(
                int(registration_start_ms) / 1000.0, tz=UTC
            ).replace(tzinfo=None)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid registrationStartAt: {exc}") from exc

    require_real_name = bool(payload.get("requireRealName", False))

    min_team_size_raw = payload.get("minTeamSize")
    max_team_size_raw = payload.get("maxTeamSize")
    min_team_size: int | None = None
    max_team_size: int | None = None
    if min_team_size_raw is not None:
        try:
            min_team_size = int(min_team_size_raw)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid minTeamSize: {exc}") from exc
    if max_team_size_raw is not None:
        try:
            max_team_size = int(max_team_size_raw)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid maxTeamSize: {exc}") from exc

    # Kotlin 行为：只有 TEAM 类型任务才允许设置 team size 相关字段。
    if submitter_type != 1 and (min_team_size is not None or max_team_size is not None):
        raise BadRequestError("minTeamSize and maxTeamSize can only be set for TEAM type tasks.")

    rank_raw = payload.get("rank")
    rank: int | None = None
    if rank_raw is not None:
        try:
            rank = int(rank_raw)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid rank: {exc}") from exc

    category_id_raw = payload.get("categoryId")
    category_id: int | None = None
    if category_id_raw is not None:
        try:
            category_id = int(category_id_raw)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid categoryId: {exc}") from exc

    team_locking_policy = payload.get("teamLockingPolicy") or "NO_LOCK"
    if team_locking_policy not in {"NO_LOCK", "LOCK_ON_APPROVAL"}:
        raise BadRequestError(f"Invalid teamLockingPolicy: {team_locking_policy}")

    topics_raw = payload.get("topics") or []
    topics: list[int] = []
    if isinstance(topics_raw, list):
        for t in topics_raw:
            try:
                topics.append(int(t))
            except (TypeError, ValueError):
                # 忽略无法解析的 topicId，避免因为单个坏值整体失败
                continue

    space_repo = SpaceRepository(session=db)
    category_repo = SpaceCategoryRepository(session=db)

    # 确认 space 存在并获取有效的 category id（传入或默认）
    effective_category_id = await _validate_and_get_category_id(
        space_repo=space_repo,
        category_repo=category_repo,
        space_id=space_id,
        category_id=category_id,
    )

    task_repo = TaskRepository(session=db)

    task = await task_repo.create_task(
        name=name,
        intro=intro,
        description=description,
        creator_id=auth_user.user_id,
        space_id=space_id,
        category_id=effective_category_id,
        submitter_type=submitter_type,
        deadline=deadline_dt,
        registration_start_at=registration_start_dt,
        participant_limit=participant_limit,
        default_deadline=default_deadline,
        resubmittable=resubmittable,
        editable=editable,
        rank=rank,
        require_real_name=require_real_name,
        min_team_size=min_team_size,
        max_team_size=max_team_size,
        team_locking_policy=team_locking_policy,
    )

    # 简单设置话题关联：先不做复杂校验，仅插入关系行。
    if topics:
        now = datetime.now(UTC).replace(tzinfo=None)
        for topic_id in topics:
            relation = TaskTopicsRelation(
                task_id=task.id,
                topic_id=topic_id,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            db.add(relation)
        await db.flush()

    return {
        "code": 200,
        "message": "Task created successfully.",
        "data": {
            "task": _task_to_api_model(task),
        },
    }


@router.post(
    "/{taskId}/participants",
    summary="Apply for Task (create participant)",
)
async def create_task_participant(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    member: Annotated[int | None, Query(description="Member ID (user or team)")] = None,
    payload: dict | None = None,
    db=Depends(get_db),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    """Create a TaskMembership for a given member.

    NOTE: 简化版实现：
    - member 由上游鉴权层保证是合法用户或团队；
    - 仅在 Python 侧做人数上限、重复参与与实名的基础校验。
    """
    if payload is None:
        payload = {}
    if member is None:
        member = auth_user.user_id

    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    if member != auth_user.user_id and task.creator_id != auth_user.user_id:
        raise ForbiddenError("Only task owner can add other participants")

    if task.approved != 0 and task.creator_id != auth_user.user_id:
        raise BadRequestError("Cannot join a task that is not approved")

    space_repo = SpaceRepository(session=db)
    space = await space_repo.get_by_id(task.space_id)
    if space is not None and space.enable_rank and task.rank is not None:
        rank_repo = SpaceUserRankRepository(session=db)
        user_rank = await rank_repo.get_rank(task.space_id, member)
        rank_jump = 1
        if user_rank + rank_jump < task.rank:
            raise BadRequestError(
                f"User rank ({user_rank}) is too low for this task (requires rank {task.rank - rank_jump}+)"
            )

    deadline_ms = payload.get("deadline")
    deadline_dt: datetime | None = None
    if deadline_ms is not None:
        try:
            deadline_dt = datetime.fromtimestamp(int(deadline_ms) / 1000.0, tz=UTC).replace(
                tzinfo=None
            )
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid deadline: {exc}") from exc

    email = payload.get("email")
    phone = payload.get("phone")
    apply_reason = payload.get("applyReason")
    personal_advantage = payload.get("personalAdvantage")
    remark = payload.get("remark")

    is_team = task.submitter_type == 1
    # 新建报名默认审批状态：与 Kotlin 一致使用 ApproveType.NONE
    approved = 2

    membership = await membership_service.create_membership(
        task=task,
        member_id=member,
        is_team=is_team,
        approved=approved,
        deadline=deadline_dt,
        email=email,
        phone=phone,
        apply_reason=apply_reason,
        personal_advantage=personal_advantage,
        remark=remark,
    )

    return {
        "code": 200,
        "message": "OK",
        "data": {
            "participant": _membership_to_api_model(membership),
        },
    }


@router.post(
    "/{taskId}/participations/user",
    summary="Join Task as User (self-join)",
)
async def join_task_as_user(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    payload: dict | None = None,
    db=Depends(get_db),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    """Allow authenticated user to join a task themselves."""
    if payload is None:
        payload = {}

    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    if task.approved != 0:
        raise BadRequestError("Task is not approved for participation")

    if task.submitter_type != 0:
        raise BadRequestError(
            "This endpoint is for USER tasks only. Use /participations/team for team tasks."
        )

    deadline_ms = payload.get("deadline")
    deadline_dt: datetime | None = None
    if deadline_ms is not None:
        try:
            deadline_dt = datetime.fromtimestamp(int(deadline_ms) / 1000.0, tz=UTC).replace(
                tzinfo=None
            )
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid deadline: {exc}") from exc

    membership = await membership_service.create_membership(
        task=task,
        member_id=auth_user.user_id,
        is_team=False,
        approved=2,
        deadline=deadline_dt,
        email=payload.get("email"),
        phone=payload.get("phone"),
        apply_reason=payload.get("applyReason"),
        personal_advantage=payload.get("personalAdvantage"),
        remark=payload.get("remark"),
    )

    return {
        "code": 200,
        "message": "OK",
        "data": {
            "participant": _membership_to_api_model(membership),
        },
    }


@router.post(
    "/{taskId}/participations/team",
    summary="Join Task as Team",
)
async def join_task_as_team(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    payload: dict,
    db=Depends(get_db),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    """Allow team to join a task."""
    team_id = payload.get("teamId")
    if not isinstance(team_id, int) or team_id <= 0:
        raise BadRequestError("teamId is required")

    from app.domain.team.repositories import TeamRepository

    team_repo = TeamRepository(session=db)
    if not await team_repo.is_team_member(team_id, auth_user.user_id):
        raise ForbiddenError("You must be a member of this team to register it for a task")

    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    if task.approved != 0:
        raise BadRequestError("Task is not approved for participation")

    if task.submitter_type != 1:
        raise BadRequestError(
            "This endpoint is for TEAM tasks only. Use /participations/user for user tasks."
        )

    deadline_ms = payload.get("deadline")
    deadline_dt: datetime | None = None
    if deadline_ms is not None:
        try:
            deadline_dt = datetime.fromtimestamp(int(deadline_ms) / 1000.0, tz=UTC).replace(
                tzinfo=None
            )
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid deadline: {exc}") from exc

    membership = await membership_service.create_membership(
        task=task,
        member_id=team_id,
        is_team=True,
        approved=2,
        deadline=deadline_dt,
        email=payload.get("email"),
        phone=payload.get("phone"),
        apply_reason=payload.get("applyReason"),
        personal_advantage=payload.get("personalAdvantage"),
        remark=payload.get("remark"),
    )

    return {
        "code": 200,
        "message": "OK",
        "data": {
            "participant": _membership_to_api_model(membership),
        },
    }


@router.patch(
    "/{taskId}/participants/{participantId}",
    summary="Update TaskMembership",
)
async def patch_task_participant(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    payload: dict,
    db=Depends(get_db),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    """Patch a single TaskMembership by participant id."""
    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    admin_repo = SpaceAdminRelationRepository(session=db)
    is_space_admin = await admin_repo.get_relation(task.space_id, auth_user.user_id) is not None
    is_creator = task.creator_id == auth_user.user_id
    if not is_creator and not is_space_admin:
        raise ForbiddenError("Only task owner or space admin can update participants")

    membership = await membership_service.get_membership_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
        raise NotFoundError("Participant not found")

    approved_raw = payload.get("approved")
    approved_value: int | None = None
    if approved_raw is not None:
        if not isinstance(approved_raw, str):
            raise BadRequestError("approved must be a string (APPROVED/DISAPPROVED/NONE)")
        approved_value = _map_approve_type_to_int(approved_raw)

    deadline_ms = payload.get("deadline")
    deadline_dt: datetime | None = None
    if deadline_ms is not None:
        try:
            deadline_dt = datetime.fromtimestamp(int(deadline_ms) / 1000.0, tz=UTC).replace(
                tzinfo=None
            )
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid deadline: {exc}") from exc

    reject_reason = payload.get("rejectReason")
    email = payload.get("email")
    phone = payload.get("phone")

    updated = await membership_service.update_membership(
        membership=membership,
        task=task,
        approved=approved_value,
        deadline=deadline_dt,
        reject_reason=reject_reason,
        email=email,
        phone=phone,
    )

    return {
        "code": 200,
        "message": "OK",
        "data": {
            "participant": _membership_to_api_model(updated),
        },
    }


@router.get(
    "/{taskId}",
    summary="Query Task",
)
async def get_task(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    querySpace: bool = Query(default=False),
    queryTeam: bool = Query(default=False),
    queryJoinability: bool = Query(default=False),
    querySubmittability: bool = Query(default=False),
    queryJoined: bool = Query(default=False),
    queryUserDeadline: bool = Query(default=False),
    queryTopics: bool = Query(default=False),
    db=Depends(get_db),
    service: TaskService = Depends(get_task_service),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    task = await service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError("Resource task not found", data={"type": "task", "id": task_id})

    # 权限检查：未审批任务只有空间管理员或任务创建者可以查看
    if task.approved == 2:  # NONE = 未审批
        is_creator = task.creator_id == auth_user.user_id
        is_space_admin = False
        if not is_creator:
            admin_repo = SpaceAdminRelationRepository(session=db)
            relation = await admin_repo.get_relation(task.space_id, auth_user.user_id)
            is_space_admin = relation is not None
        if not is_creator and not is_space_admin:
            raise ForbiddenError("Only space admins or task creator can view unapproved tasks")

    # participation 信息：当前实现支持 USER 类型的直接参与者，以及 TEAM 任务中用户所在的团队。
    participation: dict
    if auth_user.user_id == 0:
        participation = {
            "identities": [],
            "hasParticipation": False,
        }
    else:
        identities: list[dict] = []
        approved_rev_map = {
            0: "APPROVED",
            1: "DISAPPROVED",
            2: "NONE",
        }

        # USER 类型参与
        user_membership = await membership_service.get_user_membership(
            task_id=task_id,
            user_id=auth_user.user_id,
        )
        if user_membership is not None:
            approved_label = approved_rev_map.get(user_membership.approved, "NONE")
            identities.append(
                {
                    "id": user_membership.id,
                    "type": "USER",
                    "memberId": auth_user.user_id,
                    "canSubmit": approved_label == "APPROVED",
                    "approved": approved_label,
                }
            )

        # TEAM 类型参与（用户作为团队成员参与该任务）
        team_memberships = await membership_service.list_team_memberships_for_user(
            task_id=task_id,
            user_id=auth_user.user_id,
        )
        for membership in team_memberships:
            approved_label = approved_rev_map.get(membership.approved, "NONE")
            identities.append(
                {
                    "id": membership.id,
                    "type": "TEAM",
                    "memberId": membership.member_id,
                    # teamName 与 canSubmit 的精细逻辑后续接入 TeamService / role 判定
                    "teamName": None,
                    "canSubmit": approved_label == "APPROVED",
                    "approved": approved_label,
                }
            )

        participation = {
            "identities": identities,
            "hasParticipation": bool(identities),
        }

    # 组装 task DTO，并按当前用户补充 joined/submittable/userDeadline/participationEligibility 等字段（部分字段为简化版）。
    task_dict = _task_to_api_model(task)

    joined = False
    joined_teams: list[int] = []
    submittable: bool | None = None
    submittable_as_team: list[dict] = []
    user_deadline_ms: int | None = None
    participation_eligibility: dict | None = None

    if auth_user.user_id > 0:
        user_membership = await membership_service.get_user_membership(
            task_id=task_id,
            user_id=auth_user.user_id,
        )
        team_memberships = await membership_service.list_team_memberships_for_user(
            task_id=task_id,
            user_id=auth_user.user_id,
        )

        joined = bool(user_membership or team_memberships)
        joined_teams = [m.member_id for m in team_memberships]

        is_user_approved = bool(user_membership and user_membership.approved == 0)

        if task.submitter_type == 0:  # USER
            submittable = is_user_approved
            if user_membership and user_membership.deadline:
                user_deadline_ms = int(user_membership.deadline.timestamp() * 1000)
        elif task.submitter_type == 1:  # TEAM
            approved_team_memberships = [m for m in team_memberships if m.approved == 0]
            submittable = bool(approved_team_memberships)
            submittable_as_team = [{"id": m.member_id} for m in approved_team_memberships]
            if team_memberships and team_memberships[0].deadline:
                user_deadline_ms = int(team_memberships[0].deadline.timestamp() * 1000)

        if queryJoinability:
            participation_eligibility = await membership_service.get_participation_eligibility(
                task=task,
                user_id=auth_user.user_id,
            )

    # Fetch submissionSchema
    schema_repo = TaskSubmissionSchemaRepository(session=db)
    schema_entries = await schema_repo.list_by_task_id(task_id)
    type_map = {0: "TEXT", 1: "FILE"}
    submission_schema = [
        {"prompt": e.description, "type": type_map.get(e.type, "TEXT")} for e in schema_entries
    ]
    task_dict["submissionSchema"] = submission_schema

    # Fetch topics if queryTopics is true
    if queryTopics:
        topic_repo = TopicRepository(session=db)
        topic_entities = await topic_repo.list_by_task_id(task_id)
        topics_list = [
            {
                "id": t.id,
                "name": t.name,
            }
            for t in topic_entities
        ]
        task_dict["topics"] = topics_list

    task_dict.update(
        {
            "joined": joined,
            "joinedTeams": joined_teams,
            "submittable": submittable,
            "submittableAsTeam": submittable_as_team,
            "userDeadline": user_deadline_ms,
            "participationEligibility": participation_eligibility,
        }
    )

    enriched_task = (await _enrich_task_models(db, [task_dict], space_id=task.space_id))[0]

    return {
        "code": 200,
        "message": "OK",
        "data": {
            "task": enriched_task,
            "participation": participation,
        },
    }


@router.patch(
    "/{taskId}",
    summary="Update Task",
)
async def patch_task(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    payload: dict,
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    """Patch basic mutable fields of a task.

    当前实现对齐 Kotlin PatchTaskRequestDTO 的大部分字段，但暂不处理 submissionSchema。
    """
    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    from app.domain.space.repositories import SpaceAdminRelationRepository

    admin_repo = SpaceAdminRelationRepository(session=db)
    is_space_admin = await admin_repo.get_relation(task.space_id, auth_user.user_id) is not None
    is_creator = task.creator_id == auth_user.user_id

    if not is_creator and not is_space_admin:
        raise ForbiddenError("Only task owner or space admin can update this task")

    # 基本字符串字段
    if "name" in payload and payload["name"] is not None:
        task.name = str(payload["name"])
    if "intro" in payload and payload["intro"] is not None:
        task.intro = str(payload["intro"])
    if "description" in payload and payload["description"] is not None:
        task.description = str(payload["description"])

    # 布尔开关
    if "resubmittable" in payload and payload["resubmittable"] is not None:
        task.resubmittable = bool(payload["resubmittable"])
    if "editable" in payload and payload["editable"] is not None:
        task.editable = bool(payload["editable"])
    if "requireRealName" in payload and payload["requireRealName"] is not None:
        task.require_real_name = bool(payload["requireRealName"])

    # deadline / registrationStartAt / participantLimit 及其 hasXxx 标志
    deadline_ms = payload.get("deadline")
    has_deadline = payload.get("hasDeadline")
    if deadline_ms is not None:
        try:
            task.deadline = datetime.fromtimestamp(int(deadline_ms) / 1000.0, tz=UTC).replace(
                tzinfo=None
            )
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid deadline: {exc}") from exc
    if has_deadline is False:
        task.deadline = None

    registration_start_ms = payload.get("registrationStartAt")
    has_registration_start = payload.get("hasRegistrationStart")
    if registration_start_ms is not None:
        try:
            task.registration_start_at = datetime.fromtimestamp(
                int(registration_start_ms) / 1000.0, tz=UTC
            ).replace(tzinfo=None)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid registrationStartAt: {exc}") from exc
    if has_registration_start is False:
        task.registration_start_at = None

    participant_limit_raw = payload.get("participantLimit")
    has_participant_limit = payload.get("hasParticipantLimit")
    if participant_limit_raw is not None:
        try:
            task.participant_limit = int(participant_limit_raw)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid participantLimit: {exc}") from exc
    if has_participant_limit is False:
        task.participant_limit = None

    # 默认截止天数 / 排名
    if "defaultDeadline" in payload and payload["defaultDeadline"] is not None:
        try:
            task.default_deadline = int(payload["defaultDeadline"])
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid defaultDeadline: {exc}") from exc

    has_rank = payload.get("hasRank")
    rank_raw = payload.get("rank")
    if rank_raw is not None:
        try:
            task.rank = int(rank_raw)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid rank: {exc}") from exc
    if has_rank is False:
        task.rank = None

    # 审批状态与驳回原因 — 需要 space admin 权限，任务创建者不可自审
    if ("approved" in payload and payload["approved"] is not None) or (
        "rejectReason" in payload and payload["rejectReason"] is not None
    ):
        if not is_space_admin:
            raise ForbiddenError("Only space admins can approve or reject tasks")
        if "approved" in payload and payload["approved"] is not None:
            task.approved = _map_approve_type(str(payload["approved"]))
        if "rejectReason" in payload and payload["rejectReason"] is not None:
            task.reject_reason = str(payload["rejectReason"])

    # 团队大小限制，仅 TEAM 类型任务允许设置
    min_team_size_raw = payload.get("minTeamSize")
    max_team_size_raw = payload.get("maxTeamSize")
    min_team_size: int | None = task.min_team_size
    max_team_size: int | None = task.max_team_size
    if min_team_size_raw is not None:
        try:
            min_team_size = int(min_team_size_raw)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid minTeamSize: {exc}") from exc
    if max_team_size_raw is not None:
        try:
            max_team_size = int(max_team_size_raw)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid maxTeamSize: {exc}") from exc

    if task.submitter_type != 1 and (min_team_size is not None or max_team_size is not None):
        raise BadRequestError("minTeamSize and maxTeamSize can only be set for TEAM type tasks.")

    task.min_team_size = min_team_size
    task.max_team_size = max_team_size

    # teamLockingPolicy
    if "teamLockingPolicy" in payload and payload["teamLockingPolicy"] is not None:
        policy = str(payload["teamLockingPolicy"])
        if policy not in {"NO_LOCK", "LOCK_ON_APPROVAL"}:
            raise BadRequestError(f"Invalid teamLockingPolicy: {policy}")
        task.team_locking_policy = policy

    # categoryId 更新：需验证归属 space 且未归档/未删除
    if "categoryId" in payload and payload["categoryId"] is not None:
        try:
            new_category_id = int(payload["categoryId"])
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid categoryId: {exc}") from exc

        space_repo = SpaceRepository(session=db)
        category_repo = SpaceCategoryRepository(session=db)
        effective_category_id = await _validate_and_get_category_id(
            space_repo=space_repo,
            category_repo=category_repo,
            space_id=task.space_id,
            category_id=new_category_id,
        )
        task.category_id = effective_category_id

    # submissionSchema: 覆盖更新（先删后插）。
    if "submissionSchema" in payload and isinstance(payload["submissionSchema"], list):
        schema_repo = TaskSubmissionSchemaRepository(session=db)
        await schema_repo.replace_schema(task.id, payload["submissionSchema"])

    # 话题列表：简单覆盖语义，先全部软删除，再插入新集合。
    if "topics" in payload and isinstance(payload["topics"], list):
        topics_raw = payload["topics"] or []
        topics: list[int] = []
        for t in topics_raw:
            try:
                topics.append(int(t))
            except (TypeError, ValueError):
                continue

        # 软删除旧关系
        now = datetime.now(UTC).replace(tzinfo=None)
        rel_stmt = select(TaskTopicsRelation).where(
            TaskTopicsRelation.task_id == task.id,
            TaskTopicsRelation.deleted_at.is_(None),
        )
        result = await db.execute(rel_stmt)
        existing = list(result.scalars().all())
        for rel in existing:
            rel.deleted_at = now

        # 插入新的
        for topic_id in topics:
            rel = TaskTopicsRelation(
                task_id=task.id,
                topic_id=topic_id,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            db.add(rel)

    task.updated_at = datetime.now(UTC).replace(tzinfo=None)
    task = await task_repo.save(task)

    # Fetch submissionSchema for response
    schema_repo = TaskSubmissionSchemaRepository(session=db)
    schema_entries = await schema_repo.list_by_task_id(task.id)
    type_map = {0: "TEXT", 1: "FILE"}
    submission_schema = [
        {"prompt": e.description, "type": type_map.get(e.type, "TEXT")} for e in schema_entries
    ]
    task_response = _task_to_api_model(task)
    task_response["submissionSchema"] = submission_schema

    return {
        "code": 200,
        "message": "Task updated successfully.",
        "data": {
            "task": task_response,
        },
    }


@router.get(
    "",
    summary="Enumerate Tasks",
)
async def get_tasks(
    space: int = Query(..., alias="space", description="Space ID"),
    categoryId: int | None = Query(default=None),
    approved: str | None = Query(default=None),
    owner: int | None = Query(default=None),
    joined: bool | None = Query(default=None),
    topics: list[int] | None = Query(default=None),
    pageStart: str | None = Query(default=None),
    pageSize: int = Query(default=20, ge=1, le=100),
    sort_by: str = Query(default="updatedAt"),
    sort_order: str = Query(default="desc"),
    querySpace: bool = Query(default=False),
    queryTeam: bool = Query(default=False),
    queryJoinability: bool = Query(default=False),
    querySubmittability: bool = Query(default=False),
    queryJoined: bool = Query(default=False),
    queryUserDeadline: bool = Query(default=False),
    queryTopics: bool = Query(default=False),
    keywords: str | None = Query(default=None),
    db=Depends(get_db),
    service: TaskService = Depends(get_task_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    # 解析 sortBy / sortOrder，和 Kotlin 行为保持一致：非法值视为 400。
    if sort_by not in {"createdAt", "updatedAt", "deadline"}:
        raise BadRequestError(f"Invalid sortBy: {sort_by}")
    if sort_order not in {"asc", "desc"}:
        raise BadRequestError(f"Invalid sortOrder: {sort_order}")

    # 将 approved 字符串映射到数据库中的 SMALLINT（ApproveType ordinal）。
    approved_map = {
        "APPROVED": 0,
        "DISAPPROVED": 1,
        "NONE": 2,
    }
    approved_value: int | None = None
    if approved is not None:
        upper = approved.upper()
        if upper not in approved_map:
            raise BadRequestError(f"Invalid approved value: {approved}")
        approved_value = approved_map[upper]

    # 权限检查：查询未审批任务需要是空间管理员
    if approved_value == 2:  # NONE = 未审批
        admin_repo = SpaceAdminRelationRepository(session=db)
        relation = await admin_repo.get_relation(space, auth_user.user_id)
        if relation is None:
            raise ForbiddenError("Only space admins can view unapproved tasks")

    # owner 直接映射到 Task.creator_id。
    owner_id: int | None = owner

    # 使用 pageStart 作为 offset（字符串形式），与 Notification 的处理方式一致。
    offset = 0
    if pageStart:
        try:
            parsed = int(pageStart)
            if parsed >= 0:
                offset = parsed
        except ValueError:
            offset = 0

    tasks = await service.enumerate_tasks(
        space_id=space,
        category_id=categoryId,
        approved=approved_value,
        owner_id=owner_id,
        keywords=keywords,
        topics=topics,
        joined=joined,
        current_user_id=auth_user.user_id,
        limit=pageSize,
        offset=offset,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    items = [_task_to_api_model(t) for t in tasks]
    items = await _enrich_task_models(db, items, space_id=space)

    # 使用与 list / count 相同的过滤条件计算 total，以支持 hasMore/nextStart。
    total = await service.count_tasks(
        space_id=space,
        category_id=categoryId,
        approved=approved_value,
        owner_id=owner_id,
        keywords=keywords,
        topics=topics,
        joined=joined,
        current_user_id=auth_user.user_id,
    )
    returned = len(items)
    has_more = offset + returned < total
    next_start = str(offset + returned) if has_more and returned > 0 else None
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "tasks": items,
            "page": {
                "pageStart": pageStart or "",
                "pageSize": returned,
                "hasMore": has_more,
                "nextStart": next_start,
                "total": total,
            },
        },
    }


@router.delete(
    "/{taskId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Task",
)
async def delete_task(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> None:
    """Soft delete a task and its participants.

    NOTE: 与 Kotlin 版本类似，这里只做软删除；提交记录的删除将在后续引入 submission ORM 时一并处理。
    """
    task_repo = TaskRepository(session=db)
    membership_repo = TaskMembershipRepository(session=db)

    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    if task.creator_id != auth_user.user_id:
        raise ForbiddenError("Only task owner can delete this task")

    now = datetime.now(UTC).replace(tzinfo=None)
    task.deleted_at = now

    memberships = await membership_repo.list_memberships_for_task(task_id=task_id, approved=None)
    for m in memberships:
        m.deleted_at = now

    await task_repo.save(task)
    # membership 更新会随着同一 Session 的 flush 一并持久化


@router.delete(
    "/{taskId}/participants/{participantId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete TaskMembership",
)
async def delete_task_participant(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    db=Depends(get_db),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> None:
    """Soft delete a participant by membership id."""
    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    admin_repo = SpaceAdminRelationRepository(session=db)
    is_space_admin = await admin_repo.get_relation(task.space_id, auth_user.user_id) is not None
    is_creator = task.creator_id == auth_user.user_id

    membership = await membership_service.get_membership_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
        raise NotFoundError("Participant not found")

    is_self = not membership.is_team and membership.member_id == auth_user.user_id
    if not is_creator and not is_space_admin and not is_self:
        raise ForbiddenError(
            "Only task owner, space admin, or the participant can remove participation"
        )

    await membership_service.soft_delete_membership(membership)


@router.delete(
    "/{taskId}/participants",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete TaskMembership by member",
)
async def delete_task_participant_by_member(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    member: Annotated[int, Query(description="Member ID (user or team)")],
    db=Depends(get_db),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> None:
    """Soft delete a participant by task + member id."""
    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    admin_repo = SpaceAdminRelationRepository(session=db)
    is_space_admin = await admin_repo.get_relation(task.space_id, auth_user.user_id) is not None
    is_creator = task.creator_id == auth_user.user_id

    membership = await membership_service.get_membership_by_task_and_member(
        task_id=task_id,
        member_id=member,
    )
    if membership is None:
        raise NotFoundError("Participant not found")

    is_self = not membership.is_team and membership.member_id == auth_user.user_id
    if not is_creator and not is_space_admin and not is_self:
        raise ForbiddenError(
            "Only task owner, space admin, or the participant can remove participation"
        )

    await membership_service.soft_delete_membership(membership)


@router.patch(
    "/{taskId}/participants",
    summary="Update TaskMembership by member",
)
async def patch_task_membership_by_member(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    member: Annotated[int, Query(description="Member ID (user or team)")],
    payload: dict,
    db=Depends(get_db),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    """Patch a TaskMembership identified by (taskId, memberId) and return all participants."""
    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    admin_repo = SpaceAdminRelationRepository(session=db)
    is_space_admin = await admin_repo.get_relation(task.space_id, auth_user.user_id) is not None
    is_creator = task.creator_id == auth_user.user_id
    if not is_creator and not is_space_admin:
        raise ForbiddenError("Only task owner or space admin can update participants")

    membership = await membership_service.get_membership_by_task_and_member(
        task_id=task_id,
        member_id=member,
    )
    if membership is None:
        raise NotFoundError("Participant not found")

    approved_raw = payload.get("approved")
    approved_value: int | None = None
    if approved_raw is not None:
        if not isinstance(approved_raw, str):
            raise BadRequestError("approved must be a string (APPROVED/DISAPPROVED/NONE)")
        approved_value = _map_approve_type_to_int(approved_raw)

    deadline_ms = payload.get("deadline")
    deadline_dt: datetime | None = None
    if deadline_ms is not None:
        try:
            deadline_dt = datetime.fromtimestamp(int(deadline_ms) / 1000.0, tz=UTC).replace(
                tzinfo=None
            )
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid deadline: {exc}") from exc

    reject_reason = payload.get("rejectReason")
    email = payload.get("email")
    phone = payload.get("phone")

    await membership_service.update_membership(
        membership=membership,
        task=task,
        approved=approved_value,
        deadline=deadline_dt,
        reject_reason=reject_reason,
        email=email,
        phone=phone,
    )

    # 按 Kotlin PatchTaskMembershipByMember 语义，返回当前任务下所有参与者。
    all_memberships = await membership_service.list_memberships_for_task(
        task_id=task_id, approved=None
    )
    participants = [_membership_to_api_model(m) for m in all_memberships]

    return {
        "code": 200,
        "message": "OK",
        "data": {
            "participants": participants,
        },
    }


@router.post(
    "/{taskId}/resubmit",
    summary="Resubmit Task",
)
async def resubmit_task(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    """Resubmit a previously disapproved task for approval."""
    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    if task.creator_id != auth_user.user_id:
        raise ForbiddenError("Only the task creator can resubmit the task for approval.")

    # 仅当当前状态为 DISAPPROVED(1) 时允许重提。
    if task.approved != 1:
        raise ForbiddenError("Task is not in a state that allows resubmission.")

    task.approved = 2  # ApproveType.NONE
    task.reject_reason = ""
    task.updated_at = datetime.now(UTC).replace(tzinfo=None)
    task = await task_repo.save(task)

    return {
        "code": 200,
        "message": "OK",
        "data": {
            "task": _task_to_api_model(task),
        },
    }


@router.get(
    "/{taskId}/participants",
    summary="Get Participants",
)
async def get_task_participants(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    approved: str | None = Query(default=None),
    queryRealNameInfo: bool = Query(default=False),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    db=Depends(get_db),
) -> dict:
    """Return participants for a given task.

    NOTE: This implementation now respects the `approved` filter, but still
    ignores `queryRealNameInfo`（实名信息将在后续接入 user / real-name 体系）。
    """
    _ = queryRealNameInfo  # 占位，后续用于控制实名信息联查

    approved_value: int | None = None
    if approved is not None:
        approved_map = {
            "APPROVED": 0,
            "DISAPPROVED": 1,
            "NONE": 2,
        }
        upper = approved.upper()
        if upper not in approved_map:
            raise BadRequestError(f"Invalid approved value: {approved}")
        approved_value = approved_map[upper]

    memberships = await membership_service.list_memberships_for_task(
        task_id=task_id,
        approved=approved_value,
    )

    user_ids = [m.member_id for m in memberships if not m.is_team]
    user_map: dict = {}
    if user_ids:
        from app.domain.user.repositories import UserRepository

        user_repo = UserRepository(session=db)
        user_map = await user_repo.get_by_ids(user_ids)

    participants = []
    for m in memberships:
        participant_info = {"id": m.member_id}
        if not m.is_team and m.member_id in user_map:
            user = user_map[m.member_id]
            participant_info["username"] = user.username
        participants.append(_membership_to_api_model(m, participant_info=participant_info))

    return {
        "code": 200,
        "message": "OK",
        "data": {"participants": participants},
    }


@router.get(
    "/{taskId}/participants/{participantId}",
    summary="Get Task Participant",
)
async def get_task_participant(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
) -> dict:
    membership = await membership_service.get_membership_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
        raise NotFoundError("Participant not found")
    return {
        "code": 200,
        "message": "OK",
        "data": {"participant": _membership_to_api_model(membership)},
    }


@router.get(
    "/{taskId}/teams",
    summary="Get teams associated with a task",
)
async def get_task_teams(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    filter: str = Query(default="eligible"),
    service: TaskService = Depends(get_task_service),
    team_service: TeamService = Depends(get_team_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    """Return teams that current user can use for a team-type task.

    简化实现：
    - 仅校验该任务是 TEAM 类型；
    - 当前 `filter=eligible` 与 `filter=all` 行为一致，均返回用户参与的全部团队；
      完整 eligibility 与实名详情将在接入 TaskMembershipEligibilityService 后补齐。
    """
    task = await service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError("Task not found")
    if task.submitter_type != 1:
        raise BadRequestError(f"Task {task_id} does not support team participation.")

    if filter not in {"eligible", "all"}:
        raise BadRequestError(f"Invalid filter: {filter}")

    teams = await team_service.get_teams_of_user(user_id=auth_user.user_id)

    def _team_summary(t) -> dict:
        created_at_ms = int(t.created_at.timestamp() * 1000)
        updated_at_ms = int(t.updated_at.timestamp() * 1000)
        return {
            "id": t.id,
            "name": t.name,
            "intro": t.intro,
            "avatarId": t.avatar_id,
            "createdAt": created_at_ms,
            "updatedAt": updated_at_ms,
            "allMembersVerified": None,
            "memberRealNameStatus": None,
        }

    team_dtos = [_team_summary(t) for t in teams]

    return {
        "code": 200,
        "message": "OK",
        "data": {
            "teams": team_dtos,
        },
    }


@router.get(
    "/{taskId}/participants/{participantId}/submissions",
    summary="List Task Submissions",
)
async def get_task_submissions(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    allVersions: bool = Query(default=False),
    queryReview: bool = Query(default=False),
    reviewed: bool | None = Query(default=None),
    pageStart: int | None = Query(default=None),
    pageSize: int = Query(default=20, ge=1, le=100),
    sortBy: str = Query(default="updatedAt"),
    sortOrder: str = Query(default="desc"),
    submission_service: TaskSubmissionService = Depends(get_task_submission_service),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    task_service: TaskService = Depends(get_task_service),
    team_service: TeamService = Depends(get_team_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    """Enumerate submissions for a given task participant."""
    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)

    membership = await membership_service.get_membership_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
        raise NotFoundError.for_resource("participant", participant_id)

    is_task_owner = task.creator_id == auth_user.user_id
    is_own_participant = membership.member_id == auth_user.user_id and not membership.is_team
    is_team_member = False
    if membership.is_team:
        is_team_member = await team_service.is_team_member(membership.member_id, auth_user.user_id)
    if not is_task_owner and not is_own_participant and not is_team_member:
        raise ForbiddenError("You are not authorized to view these submissions")

    if sortBy not in {"createdAt", "updatedAt"}:
        raise BadRequestError(f"Invalid sortBy: {sortBy}")
    if sortOrder not in {"asc", "desc"}:
        raise BadRequestError(f"Invalid sortOrder: {sortOrder}")

    offset = pageStart or 0
    if offset < 0:
        offset = 0

    items, total = await submission_service.list_submissions(
        task_id=task_id,
        participant_id=participant_id,
        all_versions=allVersions,
        query_review=queryReview,
        reviewed=reviewed,
        limit=pageSize,
        offset=offset,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
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

    return {
        "code": 200,
        "message": "OK",
        "data": {"submissions": items, "page": page},
    }


@router.post(
    "/{taskId}/participants/{participantId}/submissions",
    summary="Create Submission",
)
async def post_task_submission(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    contents: list[dict],
    submission_service: TaskSubmissionService = Depends(get_task_submission_service),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    task_service: TaskService = Depends(get_task_service),
    team_service: TeamService = Depends(get_team_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)

    membership = await membership_service.get_membership_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
        raise NotFoundError.for_resource("participant", participant_id)

    if membership.approved != 0:
        raise ForbiddenError("Participant must be approved before submitting")

    if membership.is_team:
        is_member = await team_service.is_team_member(membership.member_id, auth_user.user_id)
        if not is_member:
            raise ForbiddenError("Only team members can submit for this team task")
    else:
        if membership.member_id != auth_user.user_id:
            raise ForbiddenError("Only the participant themselves can submit")

    if not task.resubmittable:
        existing, _ = await submission_service.list_submissions(
            task_id=task_id,
            participant_id=participant_id,
            all_versions=False,
            query_review=False,
            limit=1,
        )
        if existing:
            raise BadRequestError("Task does not allow resubmission")

    submission_dto = await submission_service.submit_task(
        task_id=task_id,
        participant_id=participant_id,
        submitter_id=auth_user.user_id,
        contents=contents,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {"submission": submission_dto},
    }


@router.patch(
    "/{taskId}/participants/{participantId}/submissions/{version}",
    summary="Update Submission",
)
async def patch_task_submission(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    version: Annotated[int, Path(ge=0)],
    contents: list[dict],
    submission_service: TaskSubmissionService = Depends(get_task_submission_service),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    task_service: TaskService = Depends(get_task_service),
    team_service: TeamService = Depends(get_team_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)

    if not task.editable:
        raise BadRequestError("Task does not allow editing submissions")

    membership = await membership_service.get_membership_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
        raise NotFoundError.for_resource("participant", participant_id)

    if membership.is_team:
        is_member = await team_service.is_team_member(membership.member_id, auth_user.user_id)
        if not is_member:
            raise ForbiddenError("Only team members can edit this submission")
    else:
        if membership.member_id != auth_user.user_id:
            raise ForbiddenError("Only the participant themselves can edit their submission")

    submission_dto = await submission_service.modify_submission(
        task_id=task_id,
        participant_id=participant_id,
        submitter_id=auth_user.user_id,
        version=version,
        contents=contents,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {"submission": submission_dto},
    }


@router.post(
    "/{taskId}/participants/{participantId}/submissions/{submissionId}/review",
    summary="Create Submission Review",
)
async def post_task_submission_review(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    submission_id: Annotated[int, Path(ge=1, alias="submissionId")],
    payload: dict,
    review_service: TaskSubmissionReviewService = Depends(get_task_submission_review_service),
    task_service: TaskService = Depends(get_task_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    _ = participant_id

    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)
    if task.creator_id != auth_user.user_id:
        raise ForbiddenError("Only task owner can create review")

    existing = await review_service.get_review_dto(submission_id)
    if existing.get("reviewed"):
        raise ConflictError("Review already exists for this submission")

    try:
        accepted = bool(payload["accepted"])
        score = int(payload["score"])
        comment = str(payload["comment"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BadRequestError(f"Invalid review payload: {exc}") from exc

    review_dto = await review_service.create_review(
        submission_id=submission_id,
        accepted=accepted,
        score=score,
        comment=comment,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {"review": review_dto},
    }


@router.get(
    "/{taskId}/participants/{participantId}/submissions/{submissionId}/review",
    summary="Get Submission Review",
)
async def get_task_submission_review(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    submission_id: Annotated[int, Path(ge=1, alias="submissionId")],
    review_service: TaskSubmissionReviewService = Depends(get_task_submission_review_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    _ = (task_id, participant_id, auth_user)

    review_dto = await review_service.get_review_dto(submission_id)

    if not review_dto.get("reviewed"):
        raise NotFoundError.for_resource("review", submission_id)

    return {
        "code": 200,
        "message": "OK",
        "data": {"review": review_dto},
    }


@router.patch(
    "/{taskId}/participants/{participantId}/submissions/{submissionId}/review",
    summary="Re-Review Submission",
)
async def patch_task_submission_review(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    submission_id: Annotated[int, Path(ge=1, alias="submissionId")],
    payload: dict,
    review_service: TaskSubmissionReviewService = Depends(get_task_submission_review_service),
    task_service: TaskService = Depends(get_task_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    _ = participant_id

    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)
    if task.creator_id != auth_user.user_id:
        raise ForbiddenError("Only task owner can update review")

    accepted = payload.get("accepted")
    score = payload.get("score")
    comment = payload.get("comment")

    if accepted is not None and not isinstance(accepted, bool):
        raise BadRequestError("accepted must be a boolean when provided")
    score_int: int | None = None
    if score is not None:
        try:
            score_int = int(score)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid score: {exc}") from exc

    comment_str: str | None = None
    if comment is not None:
        comment_str = str(comment)

    review_dto = await review_service.patch_review(
        submission_id=submission_id,
        accepted=accepted,
        score=score_int,
        comment=comment_str,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {"review": review_dto},
    }


@router.put(
    "/{taskId}/participants/{participantId}/submissions/{submissionId}/review",
    summary="Update Submission Review (Full Replace)",
)
async def put_task_submission_review(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    submission_id: Annotated[int, Path(ge=1, alias="submissionId")],
    payload: dict,
    review_service: TaskSubmissionReviewService = Depends(get_task_submission_review_service),
    task_service: TaskService = Depends(get_task_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    _ = participant_id

    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)
    if task.creator_id != auth_user.user_id:
        raise ForbiddenError("Only task owner can update review")

    try:
        accepted = bool(payload["accepted"])
        score = int(payload["score"])
        comment = str(payload["comment"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BadRequestError(f"Invalid review payload: {exc}") from exc

    review_dto = await review_service.patch_review(
        submission_id=submission_id,
        accepted=accepted,
        score=score,
        comment=comment,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {"review": review_dto},
    }


@router.delete(
    "/{taskId}/participants/{participantId}/submissions/{submissionId}/review",
    summary="Delete Submission Review",
)
async def delete_task_submission_review(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    submission_id: Annotated[int, Path(ge=1, alias="submissionId")],
    review_service: TaskSubmissionReviewService = Depends(get_task_submission_review_service),
    task_service: TaskService = Depends(get_task_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    _ = participant_id

    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)
    if task.creator_id != auth_user.user_id:
        raise ForbiddenError("Only task owner can delete review")

    existing = await review_service.get_review_dto(submission_id)
    if not existing.get("reviewed"):
        raise NotFoundError.for_resource("review", submission_id)

    await review_service.delete_review(submission_id=submission_id)
    return {"code": 200, "message": "OK"}


@router.post(
    "/{taskId}/ai-advice",
    summary="Request AI Advice Generation",
)
async def request_task_ai_advice(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    ai_service: TaskAIAdviceService = Depends(get_task_ai_advice_service),
) -> dict:
    try:
        status_value, quota_info = await ai_service.request_advice(
            task_id=task_id, user_id=auth_user.user_id
        )
    except QuotaExceededError:
        raise
    quota = {
        "remaining": quota_info.remaining,
        "total": quota_info.total,
        "reset_time": quota_info.reset_time.isoformat(),
    }
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "status": status_value,
            "quota": quota,
        },
    }


@router.get(
    "/{taskId}/ai-advice",
    summary="List AI Advice Records",
)
async def list_task_ai_advice(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    service: TaskAIAdviceService = Depends(get_task_ai_advice_service),
) -> dict:
    advices = await service.list_advices(task_id=task_id)
    return {
        "code": 200,
        "message": "OK",
        "data": {"advices": advices},
    }


@router.get(
    "/{taskId}/ai-advice/status",
    summary="Get AI Advice Status",
)
async def get_task_ai_advice_status(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    service: TaskAIAdviceService = Depends(get_task_ai_advice_service),
) -> dict:
    status_value = await service.get_status(task_id=task_id)
    return {
        "code": 200,
        "message": "OK",
        "data": {"status": status_value},
    }


@router.get(
    "/{taskId}/ai-advice/conversations/grouped",
    summary="List AI Advice Conversations (Grouped)",
)
async def list_ai_advice_conversations_grouped(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    service: TaskAIAdviceService = Depends(get_task_ai_advice_service),
) -> dict:
    groups = await service.list_conversations_grouped(task_id=task_id)
    return {
        "code": 200,
        "message": "OK",
        "data": {"groups": groups},
    }


@router.get(
    "/{taskId}/ai-advice/conversations/{conversationId}",
    summary="Get AI Advice Conversation",
)
async def get_ai_advice_conversation(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    conversation_id: Annotated[str, Path(alias="conversationId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: TaskAIAdviceService = Depends(get_task_ai_advice_service),
) -> dict:
    _ = task_id
    _ = auth_user
    try:
        payload = await service.get_conversation(conversation_id=conversation_id)
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    return {"code": 200, "message": "OK", "data": payload}


@router.post(
    "/{taskId}/ai-advice/conversations",
    summary="Create AI Advice Conversation",
)
async def create_ai_advice_conversation(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    payload: CreateTaskAIAdviceConversationRequest,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: TaskAIAdviceService = Depends(get_task_ai_advice_service),
) -> dict:
    question = payload.question.strip()
    if not question.strip():
        raise BadRequestError("question is required")
    context_payload = (
        payload.context.model_dump(by_alias=True, exclude_none=True) if payload.context else None
    )
    try:
        conversation, quota_info = await service.create_conversation(
            task_id=task_id,
            user_id=auth_user.user_id,
            question=question,
            parent_id=payload.parent_id,
            conversation_id=payload.conversation_id,
            context=context_payload,
        )
    except QuotaExceededError:
        raise
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    quota = {
        "remaining": quota_info.remaining,
        "total": quota_info.total,
        "reset_time": quota_info.reset_time.isoformat(),
    }
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "conversation": conversation.get("conversation"),
            "quota": quota,
        },
    }


@router.delete(
    "/{taskId}/ai-advice/conversations/{conversationId}",
    summary="Delete AI Advice Conversation",
)
async def delete_ai_advice_conversation(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    conversation_id: Annotated[str, Path(alias="conversationId")],
    service: TaskAIAdviceService = Depends(get_task_ai_advice_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    if auth_user.user_id == 0:
        raise ForbiddenError("Authentication required")
    try:
        await service.get_conversation(conversation_id=conversation_id)
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    await service.delete_conversation(conversation_id=conversation_id)
    return {"code": 200, "message": "OK"}


@router.post(
    "/{taskId}/ai-advice/conversations/stream",
    summary="Stream AI Advice Conversation (SSE)",
)
async def stream_ai_advice_conversation(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    payload: CreateTaskAIAdviceConversationRequest,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: TaskAIAdviceService = Depends(get_task_ai_advice_service),
):
    """Stream AI response via Server-Sent Events (SSE)."""
    import json as json_module

    from fastapi.responses import StreamingResponse

    from app.domain.llm.llm_client import (
        LLMAPIError,
        LLMConnectionError,
        LLMRateLimitError,
        LLMTimeoutError,
    )

    question = payload.question.strip()
    if not question:
        raise BadRequestError("question is required")

    context_payload = (
        payload.context.model_dump(by_alias=True, exclude_none=True) if payload.context else None
    )

    async def event_generator():
        try:
            async for chunk in service.stream_conversation(
                task_id=task_id,
                user_id=auth_user.user_id,
                question=question,
                conversation_id=payload.conversation_id,
                context=context_payload,
            ):
                if chunk.content:
                    data = json_module.dumps({"type": "content", "data": chunk.content})
                    yield f"data: {data}\n\n"
                if chunk.is_final and chunk.total_tokens:
                    data = json_module.dumps(
                        {
                            "type": "done",
                            "tokens": chunk.total_tokens,
                        }
                    )
                    yield f"data: {data}\n\n"
        except QuotaExceededError as exc:
            data = json_module.dumps(
                {"type": "error", "error": "quota_exceeded", "message": str(exc)}
            )
            yield f"data: {data}\n\n"
        except LLMTimeoutError as exc:
            data = json_module.dumps({"type": "error", "error": "timeout", "message": str(exc)})
            yield f"data: {data}\n\n"
        except LLMConnectionError as exc:
            data = json_module.dumps({"type": "error", "error": "connection", "message": str(exc)})
            yield f"data: {data}\n\n"
        except LLMRateLimitError as exc:
            data = json_module.dumps({"type": "error", "error": "rate_limit", "message": str(exc)})
            yield f"data: {data}\n\n"
        except LLMAPIError as exc:
            data = json_module.dumps(
                {
                    "type": "error",
                    "error": "llm_error",
                    "message": str(exc),
                    "status_code": exc.status_code,
                }
            )
            yield f"data: {data}\n\n"
        except ValueError as exc:
            data = json_module.dumps({"type": "error", "error": "not_found", "message": str(exc)})
            yield f"data: {data}\n\n"
        except Exception as exc:
            data = json_module.dumps({"type": "error", "error": "internal", "message": str(exc)})
            yield f"data: {data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
