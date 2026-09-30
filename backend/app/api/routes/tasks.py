from collections.abc import Sequence
from datetime import UTC, datetime
from io import BytesIO
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.auth.space_access import may_publish_in_space, may_teach_task
from app.core.config import settings
from app.core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    QuotaExceededError,
    SystemBusyError,
)
from app.core.storage import get_storage_backend
from app.db.session import get_db
from app.domain.attachment.models import Attachment
from app.domain.attachment.services import AttachmentService
from app.domain.feature_stats import pricing
from app.domain.gateway_chat import GatewayChat
from app.domain.llm.repositories import AIUserQuotaRepository
from app.domain.llm.services import AiAdviceService
from app.domain.service_keys import service_key
from app.domain.space.rank_service import SpaceRankService
from app.domain.space.repositories import (
    SpaceAdminRelationRepository,
    SpaceCategoryRepository,
    SpaceDomainGroupDomainRepository,
    SpaceDomainGroupRepository,
    SpaceRepository,
    SpaceUserRankRepository,
)
from app.domain.tag.repositories import TagRepository
from app.domain.task.attachment_service import (
    TaskAttachmentRepository,
    TaskAttachmentService,
)
from app.domain.task.models import (
    Task,
    TaskAttachment,
    TaskMembership,
    TaskSubmissionSchemaEntry,
    TaskTagRelation,
)
from app.domain.task.repositories import (
    AIConversationRepository,
    AIMessageRepository,
    TaskAccessDomainRepository,
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
from app.domain.task.submission_state import claim_state
from app.domain.task.task_ai_advice_service import TaskAIAdviceService
from app.domain.task.task_pdf_draft_service import (
    TaskPdfDraftService,
    task_draft_key_spec,
)
from app.domain.task.visibility_service import TaskVisibilityService
from app.domain.team.models import Team
from app.domain.team.repositories import TeamRepository
from app.domain.team.services import TeamService
from app.domain.team.summary import team_summary
from app.domain.usage.personal import PersonalCredits, Rates
from app.domain.user.repositories import (
    UserProfileRepository,
    UserRealNameRepository,
    UserRepository,
)

router = APIRouter(prefix="/tasks", tags=["Tasks"])


async def get_task_service(db=Depends(get_db)) -> TaskService:
    repo = TaskRepository(session=db)
    return TaskService(repo)


async def get_task_membership_service(db=Depends(get_db)) -> TaskMembershipService:
    from app.domain.team.repositories import TeamRepository as _TeamRepo

    repo = TaskMembershipRepository(session=db)
    realname_repo = UserRealNameRepository(session=db)
    space_repo = SpaceRepository(session=db)
    space_rank_repo = SpaceUserRankRepository(session=db)
    team_repo = _TeamRepo(session=db)
    return TaskMembershipService(
        repo=repo,
        realname_repo=realname_repo,
        space_repo=space_repo,
        space_rank_repo=space_rank_repo,
        team_repo=team_repo,
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
        session=db,
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
        session=db,
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


async def get_task_pdf_draft_service(db=Depends(get_db)) -> TaskPdfDraftService:
    key = await service_key(db, task_draft_key_spec())
    if key is None:
        raise SystemBusyError("从 PDF 生成草稿暂未开放，稍后再试。")
    return TaskPdfDraftService(
        chat=GatewayChat(
            key, settings.task_draft_model, max_tokens=settings.task_draft_max_tokens
        )
    )


class ConfirmTaskPublishFromPdfRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    drafts: list[dict]
    task_options: dict = Field(alias="taskOptions")


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    submitter_type: str = Field(..., alias="submitterType")
    resubmittable: bool
    editable: bool
    intro: str
    description: str
    space: int
    participant_limit: int | None = Field(default=None, alias="participantLimit")
    default_deadline: int = Field(default=30, alias="defaultDeadline")
    deadline: int | None = None
    registration_start_at: int | None = Field(default=None, alias="registrationStartAt")
    require_real_name: bool = Field(default=False, alias="requireRealName")
    min_team_size: int | None = Field(default=None, alias="minTeamSize")
    max_team_size: int | None = Field(default=None, alias="maxTeamSize")
    rank: int | None = None
    category_id: int | None = Field(default=None, alias="categoryId")
    team_locking_policy: str = Field(default="NO_LOCK", alias="teamLockingPolicy")
    video_url: str | None = Field(default=None, alias="videoUrl")
    topics: list[int] = Field(default_factory=list)
    submission_schema: list[dict] = Field(
        default_factory=list, alias="submissionSchema"
    )
    access_control_enabled: bool = Field(default=False, alias="accessControlEnabled")
    access_domain_group_ids: list[int] = Field(
        default_factory=list, alias="accessDomainGroupIds"
    )
    # 出题时带的材料。文件先经 ``POST /attachments`` 传上来拿到 id，建题时一次挂上
    # —— 见 ``TaskAttachmentService.attach_uploaded`` 里对这条顺序的说明。
    attachment_ids: list[int] = Field(default_factory=list, alias="attachmentIds")


PDF_DRAFT_CONTENT_FIELDS = {"name", "intro", "description"}


def _apply_pdf_task_options(
    *,
    draft: dict,
    task_options: dict,
) -> dict:
    if not isinstance(draft, dict):
        raise BadRequestError("Each draft must be an object")
    if not isinstance(task_options, dict):
        raise BadRequestError("taskOptions must be an object")

    missing_content_fields = [
        field
        for field in PDF_DRAFT_CONTENT_FIELDS
        if field not in draft or str(draft.get(field) or "").strip() == ""
    ]
    if missing_content_fields:
        raise BadRequestError(
            f"Draft missing required content fields: {', '.join(sorted(missing_content_fields))}"  # noqa: E501
        )

    merged = dict(task_options)
    for field in PDF_DRAFT_CONTENT_FIELDS:
        merged[field] = draft[field]

    if "space" not in merged and "space" in draft:
        merged["space"] = draft["space"]
    if "categoryId" not in merged and "categoryId" in draft:
        merged["categoryId"] = draft["categoryId"]

    return merged


def _pdf_attachment_ids(task_options: dict) -> list[int]:
    """``taskOptions.attachmentIds``：这一批题共用的材料，勾了才有。

    与其它字段一样是自由 dict（``ConfirmTaskPublishFromPdfRequest`` 不看里面的结
    构），所以形状自己看住：不是列表、或者元素不是整数，是请求写错了 —— 与「没带这
    个键」（不勾任何附件）不是同一件事，不能都当成空。
    """
    raw = task_options.get("attachmentIds")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise BadRequestError("attachmentIds must be a list")
    ids: list[int] = []
    for item in raw:
        try:
            ids.append(int(item))
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid attachmentIds entry: {item!r}") from exc
    return ids


class TaskParticipantRequest(BaseModel):
    """Body for joining a task (user or team). All fields optional."""

    model_config = ConfigDict(populate_by_name=True)

    deadline: int | None = None
    email: str | None = None
    phone: str | None = None
    apply_reason: str | None = Field(default=None, alias="applyReason")
    personal_advantage: str | None = Field(default=None, alias="personalAdvantage")
    remark: str | None = None


class JoinTaskAsTeamRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    team_id: int = Field(..., alias="teamId", gt=0)
    deadline: int | None = None
    email: str | None = None
    phone: str | None = None
    apply_reason: str | None = Field(default=None, alias="applyReason")
    personal_advantage: str | None = Field(default=None, alias="personalAdvantage")
    remark: str | None = None


class PatchTaskParticipantRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    approved: str | None = None
    deadline: int | None = None
    reject_reason: str | None = Field(default=None, alias="rejectReason")
    email: str | None = None
    phone: str | None = None


class PatchTaskRequest(BaseModel):
    """Partial update body for task. All fields optional (PATCH semantics)."""

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    intro: str | None = None
    description: str | None = None
    video_url: str | None = Field(default=None, alias="videoUrl")
    resubmittable: bool | None = None
    editable: bool | None = None
    require_real_name: bool | None = Field(default=None, alias="requireRealName")
    deadline: int | None = None
    has_deadline: bool | None = Field(default=None, alias="hasDeadline")
    registration_start_at: int | None = Field(default=None, alias="registrationStartAt")
    has_registration_start: bool | None = Field(
        default=None, alias="hasRegistrationStart"
    )
    participant_limit: int | None = Field(default=None, alias="participantLimit")
    has_participant_limit: bool | None = Field(
        default=None, alias="hasParticipantLimit"
    )
    default_deadline: int | None = Field(default=None, alias="defaultDeadline")
    rank: int | None = None
    has_rank: bool | None = Field(default=None, alias="hasRank")
    approved: str | None = None
    reject_reason: str | None = Field(default=None, alias="rejectReason")
    min_team_size: int | None = Field(default=None, alias="minTeamSize")
    max_team_size: int | None = Field(default=None, alias="maxTeamSize")
    team_locking_policy: str | None = Field(default=None, alias="teamLockingPolicy")
    category_id: int | None = Field(default=None, alias="categoryId")
    submission_schema: list[dict] | None = Field(default=None, alias="submissionSchema")
    topics: list[int] | None = None
    access_control_enabled: bool | None = Field(
        default=None, alias="accessControlEnabled"
    )
    access_domain_group_ids: list[int] | None = Field(
        default=None, alias="accessDomainGroupIds"
    )
    ended_at: int | None = Field(default=None, alias="endedAt")
    has_ended_at: bool | None = Field(default=None, alias="hasEndedAt")


class CreateSubmissionReviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    accepted: bool
    score: int
    comment: str


class PatchSubmissionReviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    accepted: bool | None = None
    score: int | None = None
    comment: str | None = None


_SUBMISSION_SCHEMA_TYPES = {0: "TEXT", 1: "FILE"}


def _submission_schema_to_api(
    entries: Sequence[TaskSubmissionSchemaEntry],
) -> list[dict]:
    """表单行的库形状 → 接口形状。详情与列表两处都报同一份，在这里收口。"""
    return [
        {
            "prompt": entry.description,
            "type": _SUBMISSION_SCHEMA_TYPES.get(entry.type, "TEXT"),
        }
        for entry in entries
    ]


def _ms(moment: datetime | None) -> int | None:
    """A moment as epoch milliseconds, the unit the task API speaks; None stays None."""
    return int(moment.timestamp() * 1000) if moment is not None else None


def _task_to_api_model(task: Task) -> dict:
    created_at_ms = _ms(task.created_at) or 0
    updated_at_ms = _ms(task.updated_at) or 0
    deadline_ms = _ms(task.deadline)
    # 审核痕迹是后加的两列：老题（以及还没审过的题）没有它，一律回 null ——
    # 界面上「没有审核人」与「不知道审核人」是同一件事，不做区分。
    reviewed_by = getattr(task, "reviewed_by", None)
    published_at_ms = _ms(getattr(task, "published_at", None))
    ended_at_ms = _ms(getattr(task, "ended_at", None))
    reviewed_at_ms = _ms(getattr(task, "reviewed_at", None))
    registration_start_ms = _ms(task.registration_start_at)
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
        "videoUrl": task.video_url,
        "accessControlEnabled": task.access_control_enabled,
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
        "publishedAt": published_at_ms,
        "endedAt": ended_at_ms,
        "reviewedBy": reviewed_by,
        "reviewedAt": reviewed_at_ms,
    }


async def _enrich_task_submission_schema(db, task_models: list[dict]) -> None:
    """把这一页每道题的提交表单填进列表响应（就地改）。

    列表默认不带这张表单（``_task_to_api_model`` 报空数组），因为它的调用方
    很多、多数不关心；审核页那种要显示「提交要求」的读法点名要它。
    整页一次查询，不按题各发一条。
    """
    task_ids = [
        task_model["id"]
        for task_model in task_models
        if isinstance(task_model.get("id"), int)
    ]
    if not task_ids:
        return

    grouped = await TaskSubmissionSchemaRepository(session=db).list_by_task_ids(
        task_ids
    )
    for task_model in task_models:
        model_id = task_model.get("id")
        if not isinstance(model_id, int):
            continue
        task_model["submissionSchema"] = _submission_schema_to_api(
            grouped.get(model_id, [])
        )


async def _count_distinct_participants(
    db,
    *,
    space_id: int,
    task_ids: list[int],
) -> int:
    """这些题上加起来**有多少个不同的人**领过。

    逐题的 `participants.total` 是 `TaskMembership` 的行数 —— 一个人领三道题就是 3，
    求和得到的是「领取次数」。首页那句「参与 N 人」要的是**跨题去重后的人**，而列表
    接口不给报名名单（`_enrich_task_models` 把 `participants.examples` 写死成空数组，
    且 `TaskParticipantSummary` 里没有 username），客户端拼不出来，只能在这一层算。

    `task_ids` 就是同一个响应里返回的那几道题，所以这个数和逐题的 `participants.total`
    **同一批题**：两个数字摆在同一行上，不能一个数的是这一页、另一个数的是全板。

    只在 `queryDistinctParticipants` 为真时调用：它跑的 `list_memberships_for_space`
    与 `_enrich_task_models` 里那次是同一条查询，不该让每个调 `/tasks` 的页面都付两遍。
    """
    if not task_ids:
        return 0
    wanted = set(task_ids)
    memberships = await TaskMembershipRepository(session=db).list_memberships_for_space(
        space_id
    )
    return len(
        {
            membership.member_id
            for membership in memberships
            if membership.task_id in wanted
        }
    )


async def _enrich_task_models(
    db,
    task_models: list[dict],
    *,
    space_id: int,
) -> list[dict]:
    if not task_models:
        return task_models

    task_ids = [task_model["id"] for task_model in task_models]
    creator_ids = [
        task_model.get("creator", {}).get("id") for task_model in task_models
    ]
    creator_ids = [
        creator_id for creator_id in creator_ids if isinstance(creator_id, int)
    ]

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
    categories = await category_repo.list_categories_for_space(
        space_id, include_archived=True
    )
    space = await space_repo.get_by_id(space_id)
    space_name = space.name if space is not None else "Unknown Space"

    role_name_map = {0: "OWNER", 1: "ADMIN"}
    admins_payload: list[dict] = []
    for rel in admin_relations:
        user = users.get(rel.user_id)
        profile = profiles.get(rel.user_id)
        if user is not None:
            nickname = (
                profile.nickname if profile and profile.nickname else user.username
            )
            avatar_id = profile.avatar_id if profile else None
            intro = profile.intro if profile else ""
            user_payload = {
                "id": user.id,
                "username": user.username,
                "nickname": nickname,
                "avatarId": avatar_id,
                "intro": intro,
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
        int(category.id): category.name
        for category in categories
        if getattr(category, "name", None)
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
            nickname = (
                profile.nickname if profile and profile.nickname else user.username
            )
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
            category_name_map.get(category_id) if isinstance(category_id, int) else None
        )
        task_model["category"] = {
            "id": category_id,
            "name": resolved_category_name or "Uncategorized",
        }
        task_model["categoryId"] = category_id

    # Resolve accessDomainGroupIds from stored TaskAccessDomain rows.
    access_domain_repo = TaskAccessDomainRepository(session=db)
    domain_group_domain_repo = SpaceDomainGroupDomainRepository(session=db)
    for task_model in task_models:
        task_model.setdefault("accessDomainGroupIds", [])
        if not task_model.get("accessControlEnabled"):
            continue
        domains = await access_domain_repo.list_by_task_id(task_model["id"])
        if domains:
            group_ids = await domain_group_domain_repo.list_group_ids_by_domains(
                space_id=space_id, domains=domains
            )
            task_model["accessDomainGroupIds"] = sorted(group_ids)

    return task_models


async def _enrich_task_attachment_counts(db, task_models: list[dict]) -> None:
    """把每道题挂着几个材料填进列表响应（就地改）。

    只是**一个数**：不带文件本体、不带文件名，也不问谁能下载 —— 卡片上那格「附件
    N」就靠它，而清单与下载各有自己的门（`GET /tasks/{id}/attachments`）。一个材料
    都没有的题给 0，不是缺字段：卡片按「有就列、没有就不显示」写，而 0 与「不知道」
    是两件事。

    整页一次分组计数，不按题各发一条。
    """
    task_ids = [
        task_model["id"]
        for task_model in task_models
        if isinstance(task_model.get("id"), int)
    ]
    if not task_ids:
        return
    counts = await TaskAttachmentRepository(session=db).count_live_by_task_ids(
        task_ids=task_ids
    )
    for task_model in task_models:
        model_id = task_model.get("id")
        if not isinstance(model_id, int):
            continue
        task_model["attachmentCount"] = counts.get(model_id, 0)


async def _enrich_task_topics(db, task_models: list[dict]) -> None:
    """Populate `task.topics: Topic[]` for the given task dicts in-place.

    Frontend `Task.topics` is an array of {id, name} objects, accessed via
    `task.topics.length` in TaskCard.vue, so we always return at least an
    empty array (not undefined).

    整页一次取回，不按题各发一条 —— 卡片现在每道题都显示标签，逐题各查一次就是
    一屏 20 条查询。
    """
    if not task_models:
        return
    task_ids = [
        task_model["id"]
        for task_model in task_models
        if isinstance(task_model.get("id"), int)
    ]
    grouped = await TopicRepository(session=db).list_by_task_ids(task_ids)
    for task_model in task_models:
        model_id = task_model.get("id")
        if not isinstance(model_id, int):
            continue
        task_model["topics"] = [
            {"id": t.id, "name": t.name} for t in grouped.get(model_id, [])
        ]


async def _enrich_task_user_state(
    membership_service: TaskMembershipService,
    tasks: list[Task],
    task_models: list[dict],
    *,
    user_id: int,
    query_joinability: bool,
    db,
) -> None:
    """Populate per-user task state (joined / submittable / userDeadline / ...).

    Mirrors the per-task computation in `get_task_detail` so list responses
    expose the same fields the frontend expects when query flags are set.
    """
    if user_id <= 0:
        # Anonymous viewer — set placeholders so the keys exist (matches the
        # detail endpoint response shape).
        for task_model in task_models:
            task_model.setdefault("joined", False)
            task_model.setdefault("joinedTeams", [])
            task_model.setdefault("submittable", None)
            task_model.setdefault("submittableAsTeam", [])
            task_model.setdefault("userDeadline", None)
            task_model.setdefault("participationEligibility", None)
            task_model.setdefault("myClaimStatus", None)
        return

    # Frontend Task.joinedTeams / submittableAsTeam are typed `Team[]`; the
    # leave-task UI accesses joinedTeams[0].id and .name directly. We used to
    # ship arrays of bare ids / `{id}` stubs, so the dialog rendered
    # "确定要让小队\"undefined\"退出该赛题吗？". Bulk-fetch real Team rows
    # for every team_id that shows up across the task list.
    from app.domain.team.repositories import TeamRepository as _TeamRepo

    team_repo = _TeamRepo(session=db)

    by_id = {task.id: task for task in tasks}

    # First pass: collect team_ids needed across all rows in this list.
    pending: dict[int, dict] = {}
    needed_team_ids: set[int] = set()
    for task_model in task_models:
        task_id = task_model["id"]
        user_membership = await membership_service.get_user_membership(
            task_id=task_id, user_id=user_id
        )
        team_memberships = await membership_service.list_team_memberships_for_user(
            task_id=task_id, user_id=user_id
        )
        for m in team_memberships:
            needed_team_ids.add(m.member_id)
        pending[task_id] = {
            "user_membership": user_membership,
            "team_memberships": team_memberships,
        }

    teams_map = (
        await team_repo.get_by_ids(list(needed_team_ids)) if needed_team_ids else {}
    )

    def _team_summary(team_id: int) -> dict:
        return team_summary(teams_map.get(team_id), fallback_id=team_id)

    # 我在每道题上走到哪一步（卡片上那格「我的领取档位」）。只问我**本人**那条
    # 领取：队友那条说的是小队走到哪了，不是这张卡要说的事。判决照
    # `app.domain.task.submission_state` 的 `claim_state` 算 —— 它和
    # `completion_status` 同一份优先级，但这一格不看截止时间，所以按判决直接翻，
    # 而不是读那一列。整页一次查询，不按题各发一条。
    my_membership_by_task_id = {
        task_id: state["user_membership"].id
        for task_id, state in pending.items()
        if state["user_membership"] is not None
        and state["user_membership"].approved != 1
    }
    verdicts_by_membership_id = await TaskSubmissionRepository(
        session=db
    ).list_review_verdicts_for_memberships(
        membership_ids=list(my_membership_by_task_id.values())
    )

    for task_model in task_models:
        task_id = task_model["id"]
        task = by_id.get(task_id)
        submitter_type = task.submitter_type if task is not None else 0
        user_membership = pending[task_id]["user_membership"]
        team_memberships = pending[task_id]["team_memberships"]

        # DISAPPROVED memberships should NOT show as "joined" — otherwise the
        # frontend renders "退出赛题" button for rejected applications.
        joined = bool(
            (user_membership and user_membership.approved != 1)
            or any(m.approved != 1 for m in team_memberships)
        )
        joined_teams = [_team_summary(m.member_id) for m in team_memberships]
        is_user_approved = bool(user_membership and user_membership.approved == 0)

        submittable: bool | None = None
        submittable_as_team: list[dict] = []
        user_deadline_ms: int | None = None

        if submitter_type == 0:  # USER
            submittable = is_user_approved
            if user_membership and user_membership.deadline:
                user_deadline_ms = int(user_membership.deadline.timestamp() * 1000)
        elif submitter_type == 1:  # TEAM
            approved_team_memberships = [m for m in team_memberships if m.approved == 0]
            submittable = bool(approved_team_memberships)
            submittable_as_team = [
                _team_summary(m.member_id) for m in approved_team_memberships
            ]
            if team_memberships and team_memberships[0].deadline:
                user_deadline_ms = int(team_memberships[0].deadline.timestamp() * 1000)

        participation_eligibility: dict | None = None
        if query_joinability and task is not None:
            participation_eligibility = (
                await membership_service.get_participation_eligibility(
                    task=task,
                    user_id=user_id,
                )
            )

        # 没领过的题给 null（卡片那一格整块不出现），领过的题给四档之一 ——
        # 一条提交都没有的领取是 IN_PROGRESS，不是 null：领了没交和没领是两件事。
        my_membership_id = my_membership_by_task_id.get(task_id)
        my_claim_status: str | None = None
        if my_membership_id is not None:
            my_claim_status = claim_state(
                verdicts_by_membership_id.get(my_membership_id, [])
            )

        task_model.update(
            {
                "joined": joined,
                "joinedTeams": joined_teams,
                "submittable": submittable,
                "submittableAsTeam": submittable_as_team,
                "userDeadline": user_deadline_ms,
                "participationEligibility": participation_eligibility,
                "myClaimStatus": my_claim_status,
            }
        )


def _build_participant_user_info(
    membership: TaskMembership,
    *,
    user_map: dict | None = None,
    profile_map: dict | None = None,
    team_map: dict | None = None,
) -> dict:
    """Build the user or team identity displayed on a registration."""
    user_map = user_map or {}
    profile_map = profile_map or {}
    team_map = team_map or {}
    if membership.is_team and membership.member_id in team_map:
        return team_summary(
            team_map[membership.member_id], fallback_id=membership.member_id
        )
    if not membership.is_team and membership.member_id in user_map:
        user = user_map[membership.member_id]
        profile = profile_map.get(membership.member_id)
        nickname = (
            profile.nickname
            if profile and getattr(profile, "nickname", None)
            else user.username
        )
        return {
            "id": user.id,
            "username": user.username,
            "nickname": nickname,
            "name": nickname,
            "avatarId": profile.avatar_id if profile else None,
            "intro": profile.intro if profile else "",
        }
    return {"id": membership.member_id}


def _membership_to_api_model(
    membership: TaskMembership,
    *,
    participant_info: dict | None = None,
    team: Team | None = None,
) -> dict:
    """Minimal TaskMembership representation for participants list.

    NOTE: This is a simplified view that focuses on structure. More fields
    (real name info, team members, etc.) can be added as needed.

    ``team`` 是这条报名背后的队（只由批量查过队名的调用者传）。传了才多出
    ``team`` 字段；没传（单条、PATCH 那几条路由）返回体与以前一模一样。
    """
    created_at_ms = _ms(membership.created_at) or 0
    updated_at_ms = _ms(membership.updated_at) or 0

    participant = participant_info or {"id": membership.member_id}
    member = participant

    approved_map = {0: "APPROVED", 1: "DISAPPROVED", 2: "NONE"}
    approved_str = approved_map.get(membership.approved, "NONE")

    model = {
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
        # 出题人给这个人单设的提交截止时间（批准时按「提交期限」定，之后可单独改）。
        "deadline": _ms(membership.deadline),
        "applyReason": membership.pitch or None,
    }
    if membership.is_team and team is not None:
        # 队名：看板按它给「小队构成」分桶、在名册里写下是哪支队伍。个人领取没有
        # 这个字段；团队领取但队已不在（查不到行）也没有 —— 前端据此退回「小队」。
        model["team"] = {"id": team.id, "name": team.name}
    return model


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
            raise BadRequestError(
                f"Cannot assign task to an archived category (id={cid})."
            )
        if category.deleted_at is not None:
            raise BadRequestError(
                f"Cannot assign task to a deleted category (id={cid})."
            )
        return category.id

    # When category_id is explicitly provided, validate it.
    if category_id is not None:
        return await _load_and_validate_category(category_id)

    # Otherwise, fall back to space.default_category_id.
    default_cid = await _get_space_default_category_id()
    return await _load_and_validate_category(default_cid)


async def _ensure_domain_groups_belong_to_space(
    *,
    db,
    space_id: int,
    group_ids: Sequence[int],
) -> None:
    """``accessDomainGroupIds`` 点名的每一个域组都得是**这块板**的。

    这些组解析出来的域会并进可见性判据（``TaskVisibilityService`` 把
    ``TaskAccessDomain.domain`` 直接并进读权限的 or 列表），所以拿别的板的组 id 发题，
    等于把那位管理员圈定的名单原样搬到自己这道题上。同一个请求体里的 ``categoryId``
    早就是这么收的（``_validate_and_get_category_id`` 问 ``get_by_id_and_space``，
    不属于这块板就 404）；这里补上同一条，话也照抄那一句。
    """
    known = {
        group.id
        for group in await SpaceDomainGroupRepository(session=db).list_groups(space_id)
    }
    if any(group_id not in known for group_id in group_ids):
        raise NotFoundError("Domain group not found or does not belong to space.")


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


async def _resolve_user_email_domain(db, user_id: int) -> str | None:
    user_repo = UserRepository(session=db)
    user = await user_repo.get_by_id(user_id)
    if user is None:
        return None
    if user.email_domain:
        return user.email_domain.lower()
    if user.email and "@" in user.email:
        return user.email.split("@", 1)[1].lower()
    return None


async def _ensure_task_visible_for_ordinary_user(
    *,
    db,
    task: Task,
    auth_user: AuthUserInfo,
) -> None:
    # 出题者或本版管理员不受 visibleTaskLimit 限制 —— 这道闸是给成员看的。
    if await may_teach_task(session=db, task=task, user_id=auth_user.user_id):
        return
    space_repo = SpaceRepository(session=db)
    space = await space_repo.get_by_id(task.space_id)
    if space is None:
        raise NotFoundError(
            "Resource space not found", data={"type": "space", "id": task.space_id}
        )
    task_repo = TaskRepository(session=db)
    if not await task_repo.is_task_visible_for_space_limit(
        task=task,
        visible_task_limit=space.visible_task_limit,
    ):
        raise NotFoundError(
            "Resource task not found", data={"type": "task", "id": task.id}
        )


async def _ensure_task_readable(
    *,
    db,
    task: Task,
    auth_user: AuthUserInfo,
) -> None:
    """「这道题在这个读者眼里存不存在」—— 题目详情与它的附属读路由共用的那一个判断。

    三道闸，按顺序各答一句话：

    1. **还没过审**（``approved == 2``，且未结项）：对出题者与本版管理员是草稿，
       对其他人还不该存在 —— 403；
    2. **看不见**（``TaskVisibilityService.can_view_task``）：题目自己设了可见范围
       而这个人不在里面 —— 404，与「这道题不存在」同一句话；
    3. **超出本板上限**（``visibleTaskLimit``）：对普通用户来说它就是看不见了 ——
       404。

    为什么要抽出来：材料清单（``/attachments``）是拿着 task id 取数的另一条读
    路由，它只走了第 2 道 —— 而第 2 道在 ``access_control_enabled`` 为假（题目
    默认值）时对任何登录用户都放行，于是 403 / 404 的题照旧把材料清单交出去。
    「题看不见，清单也看不见」是同一件事，只该有一处判断。
    """
    if task.approved == 2 and task.ended_at is None:  # NONE = 未审批
        if not await may_teach_task(session=db, task=task, user_id=auth_user.user_id):
            raise ForbiddenError(
                "Only space admins or task creator can view unapproved tasks"
            )
    if not await TaskVisibilityService(session=db).can_view_task(
        task=task, user_id=auth_user.user_id
    ):
        raise NotFoundError(
            "Resource task not found", data={"type": "task", "id": task.id}
        )
    await _ensure_task_visible_for_ordinary_user(db=db, task=task, auth_user=auth_user)


async def _create_task_entity(
    *,
    payload: dict | CreateTaskRequest,
    db,
    creator_user_id: int,
) -> Task:
    # Accept both dict (from PDF draft flow) and validated CreateTaskRequest
    if isinstance(payload, CreateTaskRequest):
        name = payload.name
        submitter_type = _map_submitter_type(payload.submitter_type)
        resubmittable = payload.resubmittable
        editable = payload.editable
        intro = payload.intro
        description = payload.description
        space_id = payload.space
        participant_limit = payload.participant_limit
        default_deadline = payload.default_deadline
        deadline_ms = payload.deadline
        registration_start_ms = payload.registration_start_at
        require_real_name = payload.require_real_name
        min_team_size = payload.min_team_size
        max_team_size = payload.max_team_size
        rank = payload.rank
        category_id = payload.category_id
        team_locking_policy = payload.team_locking_policy
        video_url = payload.video_url or None
        topics = payload.topics
        access_control_enabled = payload.access_control_enabled
        access_domain_group_ids = payload.access_domain_group_ids
        submission_schema = payload.submission_schema or []
    else:
        # Dict path — used by PDF-based creation flow
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
        registration_start_ms = payload.get("registrationStartAt")
        require_real_name = bool(payload.get("requireRealName", False))

        min_team_size_raw = payload.get("minTeamSize")
        max_team_size_raw = payload.get("maxTeamSize")
        min_team_size = (
            int(min_team_size_raw) if min_team_size_raw is not None else None
        )
        max_team_size = (
            int(max_team_size_raw) if max_team_size_raw is not None else None
        )

        rank_raw = payload.get("rank")
        rank = int(rank_raw) if rank_raw is not None else None

        category_id_raw = payload.get("categoryId")
        category_id = int(category_id_raw) if category_id_raw is not None else None

        team_locking_policy = payload.get("teamLockingPolicy") or "NO_LOCK"
        video_url = payload.get("videoUrl") or None

        access_control_enabled = bool(payload.get("accessControlEnabled", False))
        access_domain_group_ids_raw = payload.get("accessDomainGroupIds") or []
        access_domain_group_ids = []
        if isinstance(access_domain_group_ids_raw, list):
            for gid in access_domain_group_ids_raw:
                try:
                    access_domain_group_ids.append(int(gid))
                except (TypeError, ValueError):
                    continue

        topics_raw = payload.get("topics") or []
        topics = []
        if isinstance(topics_raw, list):
            for topic in topics_raw:
                try:
                    topics.append(int(topic))
                except (TypeError, ValueError):
                    continue

        schema_raw = payload.get("submissionSchema") or []
        submission_schema = (
            [e for e in schema_raw if isinstance(e, dict)]
            if isinstance(schema_raw, list)
            else []
        )

    if team_locking_policy not in {"NO_LOCK", "LOCK_ON_APPROVAL"}:
        raise BadRequestError(f"Invalid teamLockingPolicy: {team_locking_policy}")

    # Kotlin 行为：只有 TEAM 类型任务才允许设置 team size 相关字段。
    if submitter_type != 1 and (min_team_size is not None or max_team_size is not None):
        raise BadRequestError(
            "minTeamSize and maxTeamSize can only be set for TEAM type tasks."
        )

    deadline_dt: datetime | None = None
    if deadline_ms is not None:
        try:
            deadline_dt = datetime.fromtimestamp(int(deadline_ms) / 1000.0, tz=UTC)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid deadline: {exc}") from exc

    registration_start_dt: datetime | None = None
    if registration_start_ms is not None:
        try:
            registration_start_dt = datetime.fromtimestamp(
                int(registration_start_ms) / 1000.0, tz=UTC
            )
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid registrationStartAt: {exc}") from exc

    space_repo = SpaceRepository(session=db)
    category_repo = SpaceCategoryRepository(session=db)

    space = await space_repo.get_by_id(space_id)
    if space is None or space.review_status != "APPROVED":
        raise BadRequestError("Space must be approved before creating tasks")

    # 发题的门是「本板的成员」，判据在 ``app.auth.space_access.may_publish_in_space``
    # —— 更早它是管理员专属（收权：那时的 ``POST /tasks`` 几乎不校验，任何登录用户
    # 拿着 space id 就能发），#1783 之后放开成任何人：发题是成员的能力，上不上板才是
    # 管理员的判断（审核走 ``PATCH /tasks/{id}``，另一条判据）。放在这里而不是两个
    # 路由各写一遍，是因为 ``POST /tasks`` 与 PDF 批量发布
    # （``publish/from-pdf/confirm``）都从这里走，漏掉任一条就等于少了半道门。
    if not await may_publish_in_space(
        session=db, space_id=space_id, user_id=creator_user_id
    ):
        raise ForbiddenError("Only a member of this board can publish tasks here")

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
        creator_id=creator_user_id,
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
        access_control_enabled=access_control_enabled,
        video_url=video_url,
    )

    # Resolve domain group IDs to actual domains and persist TaskAccessDomain records
    if access_control_enabled and access_domain_group_ids:
        await _ensure_domain_groups_belong_to_space(
            db=db, space_id=space_id, group_ids=access_domain_group_ids
        )
        domain_repo = SpaceDomainGroupDomainRepository(session=db)
        groups_domains = await domain_repo.list_domains_for_groups(
            access_domain_group_ids
        )
        all_domains: list[str] = []
        for gid in access_domain_group_ids:
            all_domains.extend(groups_domains.get(gid, []))

        if all_domains:
            access_domain_repo = TaskAccessDomainRepository(session=db)
            await access_domain_repo.replace_domains(
                task_id=task.id, domains=list(dict.fromkeys(all_domains))
            )

    # 简单设置话题关联：先不做复杂校验，仅插入关系行。
    if topics:
        now = datetime.now(UTC)
        for topic_id in topics:
            relation = TaskTagRelation(
                task_id=task.id,
                tag_id=topic_id,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            db.add(relation)
        await db.flush()

    # 提交表单：发布页总会带上这张表（至少一个「提交文件」项）。建题时不写，
    # 题目的提交页就一个输入项都没有，成员无处上传 —— 和 PATCH 写的是同一张表。
    if submission_schema:
        await TaskSubmissionSchemaRepository(session=db).replace_schema(
            task.id, submission_schema
        )
        await db.flush()

    return task


@router.post(
    "",
    summary="Create Task",
)
async def create_task(
    payload: CreateTaskRequest,
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """Create a new task (simplified port of Kotlin TaskService.createTask).

    NOTE:
    - 权限：调用者必须是 ``space`` 的成员（在成员名册里，或所有者在管理员关系里），
      否则 403 —— 从前这条 route 只要求提供 space 并验证 category 归属，任何登录
      用户都能往别人的板里发题；中间一度收成「只有管理员能发」，重设计后放开为
      「板里的人都能发、所有者与管理员审」（见 ``_create_task_entity`` 里的
      ``may_publish_in_space``）；
    - submissionSchema 与 PATCH 一样写入提交表单；topics 只插关系行，不做校验。
    """
    task = await _create_task_entity(
        payload=payload,
        db=db,
        creator_user_id=auth_user.user_id,
    )

    if payload.attachment_ids:
        # 材料是随题一起发出去的，所以挂在这里做：题目已经建好（``_create_task_entity``
        # 里 flush 过），权限那道门也已经在同一个请求里过了一次。文件先由调用者经
        # ``POST /attachments`` 传上来，这里只认 id —— PDF 批量发布那条路（附件在服务
        # 端手上）走的是 ``attach_uploaded_to_tasks``，同一批校验，只是能挂到多道题上。
        await _task_attachment_service(db).attach_uploaded(
            task=task,
            user_id=auth_user.user_id,
            attachment_ids=payload.attachment_ids,
        )

    # 写到这里就完了 —— 题目、话题关系行、提交表单、材料都已落库，下面全是读。
    # 先提交再构造响应：``get_db`` 的提交在 ``yield`` 的退出码里，而那段跑在响应
    # 发出**之后**（FastAPI 0.137 的 ``request_stack`` 在 ``await response(...)``
    # 之后才关），不在这里提交，客户端拿到响应时这道题还没落地，紧接着来读它的
    # 请求就找不到（同 ``spaces.create_space``，合并队列 run 36296605673 实测）。
    await db.commit()

    task_model = _task_to_api_model(task)
    task_model = (await _enrich_task_models(db, [task_model], space_id=task.space_id))[
        0
    ]

    return {
        "code": 200,
        "message": "Task created successfully.",
        "data": {
            "task": task_model,
        },
    }


def _task_attachment_service(db) -> TaskAttachmentService:
    return TaskAttachmentService(session=db, storage=get_storage_backend())


def _attachment_service(db) -> AttachmentService:
    """还没挂到题上的文件那一层（``POST /attachments`` 用的同一个服务）。

    ``from_session`` 而不是自己造它的 repository：这里在题目这一域的地盘上，
    摸附件那一域的 repository 正是 ``test_domain_import_guard.py`` 拦的那一条。
    """
    return AttachmentService.from_session(session=db, storage=get_storage_backend())


def _uploaded_attachment_to_api(attachment: Attachment) -> dict:
    """一个**还没挂到任何题上**的文件在预览响应里的样子。

    与 ``_task_attachment_to_api`` 是同一套字段来源（名字、大小、类型都取自
    ``meta``），只是没有那两样要有关联行才成立的东西：下载计数、挂上来的时间。同样
    **不带 url** —— 存储给的是直链，发出去就等于绕开下载那道门。
    """
    return {
        "id": attachment.id,
        "name": attachment.meta.get("filename") or f"attachment_{attachment.id}",
        "size": attachment.meta.get("size", 0),
        "contentType": attachment.meta.get("contentType", "application/octet-stream"),
    }


async def _require_task(db, task_id: int) -> Task:
    task = await TaskRepository(session=db).get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")
    return task


def _task_attachment_to_api(*, attachment: Attachment, link: TaskAttachment) -> dict:
    """一个附件在接口上的样子。

    **不带 url**：存储给的是直链（本地 ``/uploads/...``、S3 公开地址），把它发出去
    就等于把「下载限人」这道门绕过去了。要文件就走下面那个下载端点，门在那里。
    """
    return {
        "id": attachment.id,
        "name": attachment.meta.get("filename") or f"attachment_{attachment.id}",
        "size": attachment.meta.get("size", 0),
        "contentType": attachment.meta.get("contentType", "application/octet-stream"),
        "uploaderId": attachment.meta.get("uploaderId"),
        "downloadCount": link.download_count,
        "createdAt": int(link.created_at.timestamp() * 1000),
    }


@router.get(
    "/{taskId}/attachments",
    summary="List Task Attachments",
)
async def list_task_attachments(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """清单对**看得见这道题的人**都可见，能不能下载单独用一个标志告诉前端。

    判据放在服务里，两件事一起算：看得见才给清单（否则 403），能不能下载按
    「出题人 / 板管理员 / 已领取者」。前端只据此决定那行显示「下载」还是
    「领取这道题之后才能下载」，不自己猜。

    「看得见这道题」用的是题目详情那三道闸（``_ensure_task_readable``），不是
    服务里那条更宽的 ``can_view_task``：后者在题目没开可见范围时对任何登录用户
    都放行，于是未审批（403）与超出板上限（404）的题会在这里把材料清单交出去。
    清单不比题更公开 —— 文件名常常就是答案，而「有没有清单」本身就是那道题的
    探针。
    """
    task = await _require_task(db, task_id)
    await _ensure_task_readable(db=db, task=task, auth_user=auth_user)
    files, links, can_download = await _task_attachment_service(db).list_for_task(
        task=task, user_id=auth_user.user_id
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "attachments": [
                _task_attachment_to_api(attachment=attachment, link=link)
                for attachment, link in zip(files, links, strict=True)
            ],
            "canDownload": can_download,
        },
    }


@router.post(
    "/{taskId}/attachments",
    summary="Upload Task Attachment",
    status_code=201,
)
async def upload_task_attachment(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    file: Annotated[UploadFile, File(...)],
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """传一个文件并挂到这道题上：出题人本人或板管理员。

    大小不在这里判：这条路由与 ``POST /attachments`` 走的是同一个
    ``AttachmentService.upload``，单份文件的上限在那一处判一次就够
    （``settings.attachment_max_bytes``），报出去的也是那个数
    （``GET /attachments/limits``）。
    """
    task = await _require_task(db, task_id)
    attachment, link = await _task_attachment_service(db).add(
        task=task,
        user_id=auth_user.user_id,
        file=file.file,
        filename=file.filename or "unnamed",
        content_type=file.content_type,
    )
    return {
        "code": 201,
        "message": "Created",
        "data": {
            "attachment": _task_attachment_to_api(attachment=attachment, link=link)
        },
    }


@router.get(
    "/{taskId}/attachments/{attachmentId}/download",
    summary="Download Task Attachment",
)
async def download_task_attachment(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    attachment_id: Annotated[int, Path(ge=1, alias="attachmentId")],
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> Response:
    """下载一道题的材料。

    先过题目详情那三道闸（``_ensure_task_readable``），再看「你来不来得到」：
    一道 403 / 404 的题，它的材料连「拿不到（403）」这个回答都不该给 —— 对读者
    来说这道题不存在，回答里不该有它的 id 之外的任何东西。
    """
    task = await _require_task(db, task_id)
    await _ensure_task_readable(db=db, task=task, auth_user=auth_user)
    content, filename, content_type = await _task_attachment_service(db).download(
        task=task,
        user_id=auth_user.user_id,
        attachment_id=attachment_id,
    )
    # ``filename*=UTF-8''…`` 而不是裸引号：中文文件名直接写进 header 会让
    # Starlette 按 latin-1 编码时报错（下载一个中文名的材料变成 500）。
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": (
                "attachment; filename*=UTF-8''" + quote(filename, safe="")
            )
        },
    )


@router.delete(
    "/{taskId}/attachments/{attachmentId}",
    summary="Remove Task Attachment",
    status_code=204,
)
async def remove_task_attachment(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    attachment_id: Annotated[int, Path(ge=1, alias="attachmentId")],
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> None:
    await _task_attachment_service(db).remove(
        task=await _require_task(db, task_id),
        user_id=auth_user.user_id,
        attachment_id=attachment_id,
    )


@router.post(
    "/publish/from-pdf/preview",
    summary="Preview Task Drafts From PDF",
)
async def preview_task_from_pdf(
    space_id: Annotated[int, Form(alias="spaceId")],
    pdf_file: Annotated[UploadFile, File(alias="file")],
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    draft_service: TaskPdfDraftService = Depends(get_task_pdf_draft_service),
    category_id: Annotated[int | None, Form(alias="categoryId")] = None,
    template_index: Annotated[int, Form(alias="templateIndex")] = 0,
    forced_submitter_type: Annotated[str | None, Form(alias="submitterType")] = None,
    max_tasks: Annotated[int, Form(alias="maxTasks")] = 5,
) -> dict:
    filename = (pdf_file.filename or "").lower()
    content_type = (pdf_file.content_type or "").lower()
    if not filename.endswith(".pdf") and "pdf" not in content_type:
        raise BadRequestError("Only PDF file is supported")
    if max_tasks < 1 or max_tasks > 20:
        raise BadRequestError("maxTasks must be between 1 and 20")

    pdf_bytes = await pdf_file.read()
    if not pdf_bytes:
        raise BadRequestError("Uploaded PDF is empty")
    if len(pdf_bytes) > 15 * 1024 * 1024:
        raise BadRequestError("PDF file is too large (max 15MB)")

    space_repo = SpaceRepository(session=db)
    space = await space_repo.get_by_id(space_id)
    if space is None:
        raise NotFoundError("Space not found")

    # 发题的门，与 ``_create_task_entity`` 是同一句、同一处口径
    # （``may_publish_in_space``：「这个板里的人都能发」）。预览是发题的前半截 ——
    # `confirm` 那条路逐条落进 `_create_task_entity` 时已经过这道门，只有预览这一
    # 条漏着：从前只 `require_auth_user`，于是板外的登录用户拿别人的 `spaceId`
    # （小整数、可枚举）就能让模型为这块板花掉 token，并把 `task_templates` 原样
    # 读回去（返回体的 `templateUsed`）—— 而同一份模板在 `GET /spaces/{spaceId}`
    # 上要先 ``_ensure_space_visible`` 才看得到。
    #
    # 门放在读 PDF 之前：挡的是「谁可以让这块板干活」，不是「响应里少写几个字段」。
    # 措辞照抄发题那道门（它自己那句「board manager」与判据的注释在
    # `space_access.may_publish_in_space` 里已有交代）——两处一句话，不另立说法。
    if not await may_publish_in_space(
        session=db, space_id=space_id, user_id=auth_user.user_id
    ):
        raise ForbiddenError("Only a board manager can publish tasks here")

    resolved_category_id = category_id
    if resolved_category_id is None:
        category_repo = SpaceCategoryRepository(session=db)
        categories = await category_repo.list_categories_for_space(
            space_id, include_archived=False
        )
        for category in categories:
            if category.name.strip().lower() == "general":
                resolved_category_id = category.id
                break

    default_topic_ids: list[int] = []
    global_topic_repo = TagRepository(session=db)
    default_topic = await global_topic_repo.get_by_name("计算机系统")
    if default_topic is not None:
        default_topic_ids.append(default_topic.id)

    # The publisher asked for this, so it comes out of their personal credits.
    rates = Rates.of(draft_service.model, await pricing.model_rates())
    if rates is None:
        raise SystemBusyError("从 PDF 生成草稿暂未开放，稍后再试。")
    credits = PersonalCredits(db)
    balance = await credits.balance(auth_user.user_id)
    if balance.credits_remaining <= 0:
        raise QuotaExceededError(balance.exhausted_message())

    template = draft_service.pick_template(space.task_templates or [], template_index)
    try:
        (
            drafts,
            token_used,
            illustrations,
        ) = await draft_service.generate_task_payloads_from_pdf(
            pdf_bytes=pdf_bytes,
            template=template,
            space_id=space_id,
            category_id=resolved_category_id,
            forced_submitter_type=forced_submitter_type,
            user_id=auth_user.user_id,
            default_topic_ids=default_topic_ids,
            max_tasks=max_tasks,
        )
    finally:
        # Every page the model answered was paid for, drafts or not; committed
        # here so a preview that fails afterwards still leaves the charge.
        spent = draft_service.spent
        if spent.total_tokens:
            await credits.charge(
                auth_user.user_id,
                model=draft_service.model,
                rates=rates,
                input_tokens=spent.prompt_tokens,
                output_tokens=spent.completion_tokens,
                cache_read_tokens=spent.cache_read_tokens,
                cache_write_tokens=spent.cache_write_tokens,
                kind="task_pdf_draft",
            )
        await db.commit()

    # 解析出来的东西落成**发布者本人名下**的附件行，把 id 交回给前端去勾：原 PDF 与
    # 那几张插图在服务端手上，只有这里能登记它们。挂在 ``meta.uploaderId`` 上的名字
    # 必须是调用者 —— 确认发布那一步拿 ``TaskAttachmentService`` 挂文件，它有一道校
    # 验是「只能挂自己上传的文件」（附件 id 是可猜的连续整数，不校验就等于把别人的
    # 文件挂到自己的题上）。服务端自己建的行如果不署名，这一批文件会被自己挡住。
    #
    # 提前落库的代价是「解析了却没发布」时留下没人引用的文件，这与素材库、PDF 导入
    # 抽图今天的处境一样（``TaskAttachmentService.attach_uploaded`` 里那段说明）。
    attachments = _attachment_service(db)
    pdf_attachment = await attachments.upload(
        file=BytesIO(pdf_bytes),
        filename=pdf_file.filename or "document.pdf",
        content_type="application/pdf",
        uploader_id=auth_user.user_id,
    )
    illustration_attachments = [
        await attachments.register_stored(
            filename=illustration.filename,
            content_type=illustration.content_type,
            storage_key=illustration.storage_key,
            url=illustration.url,
            size=illustration.size,
            uploader_id=auth_user.user_id,
            file_hash=illustration.file_hash,
        )
        for illustration in illustrations
    ]

    return {
        "code": 200,
        "message": "Task drafts previewed from PDF successfully.",
        "data": {
            "drafts": drafts,
            "templateUsed": template,
            "tokenUsed": token_used,
            # 可以勾的东西：原 PDF 一份，抽出的插图若干张。前端照这个画勾选框，
            # 勾中的 id 由确认发布那条请求带回来。
            "attachments": {
                "pdf": _uploaded_attachment_to_api(pdf_attachment),
                "images": [
                    _uploaded_attachment_to_api(attachment)
                    for attachment in illustration_attachments
                ],
            },
        },
    }


@router.post(
    "/publish/from-pdf/confirm",
    summary="Confirm Publish Task Drafts From PDF",
)
async def confirm_publish_task_from_pdf(
    payload: ConfirmTaskPublishFromPdfRequest,
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    drafts = payload.drafts
    task_options = payload.task_options
    if not drafts:
        raise BadRequestError("drafts is required")
    if len(drafts) > 20:
        raise BadRequestError("At most 20 drafts can be published at once")

    # 勾中的附件跟着**每一道**生成出来的题走（不是随机分给某一道）：预览那一步把原
    # PDF 与抽出的插图报了回来，人在这里勾「原 PDF / 抽出的插图」，所以这一批题拿到
    # 的是同一份材料。不勾就一个都不带 —— 默认行为与这条路今天的样子完全一样。
    attachment_ids = _pdf_attachment_ids(task_options)
    if attachment_ids:
        # **先校验、后建题**：一个挂不上的 id（不存在的、别人的、已经在别的题上的）
        # 让整条请求立刻失败，一道题都不建。异常时 ``get_db`` 会回滚整个请求，所以顺
        # 序本身不改变结果；但「先把题造出来、再靠回滚收走」把一批没材料的题压在一个
        # 请求级保证上，而这件事本来可以不做。
        await _task_attachment_service(db).ensure_attachable(
            user_id=auth_user.user_id, attachment_ids=attachment_ids
        )

    created_tasks: list[Task] = []
    space_id: int | None = None
    for draft in drafts:
        task_payload = _apply_pdf_task_options(draft=draft, task_options=task_options)
        created = await _create_task_entity(
            payload=task_payload,
            db=db,
            creator_user_id=auth_user.user_id,
        )
        created_tasks.append(created)
        if space_id is None and "space" in task_payload:
            try:
                space_id = int(task_payload["space"])
            except (TypeError, ValueError):
                pass

    if attachment_ids:
        # 一道题一次挂完再进下一道？不 —— 校验（存在 / 是我的 / 还没挂在别处）在这一
        # 批**开始之前**判一次，理由见 ``attach_uploaded_to_tasks``。校验不过就整个请
        # 求回滚（``get_db`` 在异常时 rollback），不会留下一半带材料、一半不带的题。
        await _task_attachment_service(db).attach_uploaded_to_tasks(
            tasks=created_tasks,
            user_id=auth_user.user_id,
            attachment_ids=attachment_ids,
        )

    # Commit before answering. ``get_db`` commits in its teardown, which FastAPI
    # runs after the response has gone out, so a client told the drafts are
    # published could open the review queue on its next request and not find them.
    # Same reason as the commit in ``create_task``.
    await db.commit()

    task_models = [_task_to_api_model(task) for task in created_tasks]
    if space_id is not None:
        task_models = await _enrich_task_models(db, task_models, space_id=space_id)

    return {
        "code": 200,
        "message": "Task drafts published successfully.",
        "data": {
            "tasks": task_models,
            "count": len(created_tasks),
        },
    }


async def _participation_response(db, task, membership, auth_user) -> dict:
    from app.domain.project.services import ProjectService

    owner_id = auth_user.user_id if membership.is_team else membership.member_id
    if membership.is_team:
        from app.domain.team.models import TeamMemberRole

        members = await TeamRepository(db).list_members_of_team(membership.member_id)
        if not any(member.user_id == owner_id for member in members):
            owner_id = next(
                (
                    member.user_id
                    for member in members
                    if member.role == TeamMemberRole.OWNER
                ),
                None,
            )
            if owner_id is None:
                raise NotFoundError("Team owner not found")
    owner = await UserRepository(session=db).get_by_id(owner_id)
    if owner is None:
        raise NotFoundError("Participant user not found")
    project = await ProjectService(db).for_participation(
        task=task, membership=membership, owner_handle=owner.username
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "participant": _membership_to_api_model(membership),
            "project": {
                "id": str(project.id),
                "name": project.name,
                "root_topic_id": str(project.root_topic_id),
                "team_id": project.team_id,
            },
        },
    }


@router.post(
    "/{taskId}/participants",
    summary="Apply for Task (create participant)",
)
async def create_task_participant(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    member: Annotated[int | None, Query(description="Member ID (user or team)")] = None,
    payload: TaskParticipantRequest | None = None,
    db=Depends(get_db),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """Create a TaskMembership for a given member.

    NOTE: 简化版实现：
    - member 由上游鉴权层保证是合法用户或团队；
    - 仅在 Python 侧做人数上限、重复参与与实名的基础校验。
    """
    if payload is None:
        payload = TaskParticipantRequest()
    if member is None:
        member = auth_user.user_id

    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    # 出题者或本版管理员可以替成员报名，也可以把没审过的题先加进课程，
    # 不必等它 approved —— 这两条都是「管理员对这道题能做的事」，和评审同一个判据。
    is_teacher = await may_teach_task(session=db, task=task, user_id=auth_user.user_id)
    if member != auth_user.user_id and not is_teacher:
        raise ForbiddenError("Only task owner can add other participants")

    if task.approved != 0 and not is_teacher:
        raise BadRequestError("Cannot join a task that is not approved")
    if task.ended_at is not None:
        raise BadRequestError("Cannot join an ended task")

    # 可见性检查：禁止"看不到但能加入"
    visibility_service = TaskVisibilityService(session=db)
    can_view = await visibility_service.can_view_task(
        task=task, user_id=auth_user.user_id
    )
    if not can_view:
        raise NotFoundError(
            "Resource task not found", data={"type": "task", "id": task_id}
        )
    await _ensure_task_visible_for_ordinary_user(db=db, task=task, auth_user=auth_user)

    # Rank check mirrors NT TaskMembershipEligibilityService.checkRankEligibility:
    # only gates the request when APPLICATION_RANK_CHECK_ENFORCED=true. The
    # eligibility service already respects this flag; the join route used to
    # block unconditionally and rejected every user whose space_user_rank row
    # didn't exist (most of them), making "领取赛题" impossible by default.
    if settings.rank_check_enforced:
        space_repo = SpaceRepository(session=db)
        space = await space_repo.get_by_id(task.space_id)
        if space is not None and space.enable_rank and task.rank is not None:
            rank_repo = SpaceUserRankRepository(session=db)
            user_rank = await rank_repo.get_rank(task.space_id, member)
            rank_jump = settings.rank_jump
            if user_rank + rank_jump < task.rank:
                raise BadRequestError(
                    f"User rank ({user_rank}) is too low for this task "
                    f"(requires rank {task.rank - rank_jump}+)"
                )

    deadline_dt: datetime | None = None
    if payload.deadline is not None:
        try:
            deadline_dt = datetime.fromtimestamp(int(payload.deadline) / 1000.0, tz=UTC)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid deadline: {exc}") from exc

    is_team = task.submitter_type == 1
    if is_team and not await TeamRepository(session=db).is_team_member(
        member, auth_user.user_id
    ):
        # 这条路由的 ``member`` 在 TEAM 题上是**队伍 id**，而没带它时默认成了调用者
        # 自己的 **user id** —— 两张表各自自增，撞上就等于把别人的队伍报了上去。
        # 与 ``join_task_as_team`` 同一句话：要把一支队伍报上去，你先得是那支队的人。
        raise ForbiddenError(
            "You must be a member of this team to register it for a task"
        )
    # 新建报名默认审批状态：与 Kotlin 一致使用 ApproveType.NONE
    approved = 2

    membership = await membership_service.create_membership(
        task=task,
        member_id=member,
        is_team=is_team,
        approved=approved,
        deadline=deadline_dt,
        email=payload.email,
        phone=payload.phone,
        apply_reason=payload.apply_reason,
        personal_advantage=payload.personal_advantage,
        remark=payload.remark,
    )

    return await _participation_response(db, task, membership, auth_user)


@router.post(
    "/{taskId}/participations/user",
    summary="Join Task as User (self-join)",
)
async def join_task_as_user(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    payload: TaskParticipantRequest | None = None,
    db=Depends(get_db),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """Allow authenticated user to join a task themselves."""
    if payload is None:
        payload = TaskParticipantRequest()

    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    if task.approved != 0:
        raise BadRequestError("Task is not approved for participation")
    if task.ended_at is not None:
        raise BadRequestError("Cannot join an ended task")

    if task.submitter_type != 0:
        raise BadRequestError(
            "This endpoint is for USER tasks only. Use /participations/team for team tasks."  # noqa: E501
        )

    # 可见性检查：禁止"看不到但能加入"
    visibility_service = TaskVisibilityService(session=db)
    can_view = await visibility_service.can_view_task(
        task=task, user_id=auth_user.user_id
    )
    if not can_view:
        raise NotFoundError(
            "Resource task not found", data={"type": "task", "id": task_id}
        )
    await _ensure_task_visible_for_ordinary_user(db=db, task=task, auth_user=auth_user)

    deadline_dt: datetime | None = None
    if payload.deadline is not None:
        try:
            deadline_dt = datetime.fromtimestamp(int(payload.deadline) / 1000.0, tz=UTC)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid deadline: {exc}") from exc

    membership = await membership_service.create_membership(
        task=task,
        member_id=auth_user.user_id,
        is_team=False,
        approved=2,
        deadline=deadline_dt,
        email=payload.email,
        phone=payload.phone,
        apply_reason=payload.apply_reason,
        personal_advantage=payload.personal_advantage,
        remark=payload.remark,
    )
    response = await _participation_response(db, task, membership, auth_user)
    # Commit before answering (the claim and the project it opens). ``get_db``
    # commits in its teardown, which FastAPI runs after the response has gone
    # out, so the task owner listing the participants right after a claim could
    # find no pending record yet. Same reason as the commit in ``create_task``.
    await db.commit()
    return response


@router.post(
    "/{taskId}/participations/team",
    summary="Join Task as Team",
)
async def join_task_as_team(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    payload: JoinTaskAsTeamRequest,
    db=Depends(get_db),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """Allow team to join a task."""
    from app.domain.team.repositories import TeamRepository

    team_repo = TeamRepository(session=db)
    if not await team_repo.is_team_member(payload.team_id, auth_user.user_id):
        raise ForbiddenError(
            "You must be a member of this team to register it for a task"
        )

    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    if task.approved != 0:
        raise BadRequestError("Task is not approved for participation")
    if task.ended_at is not None:
        raise BadRequestError("Cannot join an ended task")

    if task.submitter_type != 1:
        raise BadRequestError(
            "This endpoint is for TEAM tasks only. Use /participations/user for user tasks."  # noqa: E501
        )

    # 可见性检查：禁止"看不到但能加入"
    visibility_service = TaskVisibilityService(session=db)
    can_view = await visibility_service.can_view_task(
        task=task, user_id=auth_user.user_id
    )
    if not can_view:
        raise NotFoundError(
            "Resource task not found", data={"type": "task", "id": task_id}
        )
    await _ensure_task_visible_for_ordinary_user(db=db, task=task, auth_user=auth_user)

    deadline_dt: datetime | None = None
    if payload.deadline is not None:
        try:
            deadline_dt = datetime.fromtimestamp(int(payload.deadline) / 1000.0, tz=UTC)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid deadline: {exc}") from exc

    membership = await membership_service.create_membership(
        task=task,
        member_id=payload.team_id,
        is_team=True,
        approved=2,
        deadline=deadline_dt,
        email=payload.email,
        phone=payload.phone,
        apply_reason=payload.apply_reason,
        personal_advantage=payload.personal_advantage,
        remark=payload.remark,
    )
    # Commit before answering, as in ``join_task_as_user``: the publisher lists
    # the pending claims on the next request.
    await db.commit()

    return await _participation_response(db, task, membership, auth_user)


@router.patch(
    "/{taskId}/participants/{participantId}",
    summary="Update TaskMembership",
)
async def patch_task_participant(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    payload: PatchTaskParticipantRequest,
    db=Depends(get_db),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """Patch a single TaskMembership by participant id."""
    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    if not await may_teach_task(session=db, task=task, user_id=auth_user.user_id):
        raise ForbiddenError("Only task owner or space admin can update participants")

    membership = await membership_service.get_membership_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
        raise NotFoundError("Participant not found")

    approved_value: int | None = None
    if payload.approved is not None:
        approved_value = _map_approve_type_to_int(payload.approved)

    deadline_dt: datetime | None = None
    if payload.deadline is not None:
        try:
            deadline_dt = datetime.fromtimestamp(int(payload.deadline) / 1000.0, tz=UTC)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid deadline: {exc}") from exc

    updated = await membership_service.update_membership(
        membership=membership,
        task=task,
        approved=approved_value,
        deadline=deadline_dt,
        reject_reason=payload.reject_reason,
        email=payload.email,
        phone=payload.phone,
    )

    from app.domain.project.services import ProjectService

    await ProjectService(db).activate_participation(task=task, membership=updated)
    # Commit before answering: the participant told they are approved can reload
    # the task on their next request and must see it as theirs (download and
    # submit). Same reason as the commit in ``create_task``.
    await db.commit()

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
        raise NotFoundError(
            "Resource task not found", data={"type": "task", "id": task_id}
        )

    # 权限检查：未审批的题只有出题者与本版管理员能看 —— 一道还没过审的题在他们手上
    # 是「草稿」，对其他人来说还不该存在。三道闸都在 `_ensure_task_readable` 里，
    # 题目的附属读路由（材料清单）走同一个判断。
    await _ensure_task_readable(db=db, task=task, auth_user=auth_user)

    # participation 信息：当前实现支持 USER 类型的直接参与者，以及 TEAM 任务中用户所在的团队。  # noqa: E501
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
                    "deadline": int(user_membership.deadline.timestamp() * 1000)
                    if user_membership.deadline
                    else None,
                    "canSubmit": approved_label == "APPROVED",
                    "approved": approved_label,
                }
            )

        # TEAM 类型参与（用户作为团队成员参与该任务）
        team_memberships = await membership_service.list_team_memberships_for_user(
            task_id=task_id,
            user_id=auth_user.user_id,
        )
        identity_teams = await TeamRepository(session=db).get_by_ids(
            [membership.member_id for membership in team_memberships]
        )
        for membership in team_memberships:
            approved_label = approved_rev_map.get(membership.approved, "NONE")
            identities.append(
                {
                    "id": membership.id,
                    "type": "TEAM",
                    "memberId": membership.member_id,
                    "teamName": identity_teams[membership.member_id].name
                    if membership.member_id in identity_teams
                    else None,
                    "deadline": int(membership.deadline.timestamp() * 1000)
                    if membership.deadline
                    else None,
                    "canSubmit": approved_label == "APPROVED",
                    "approved": approved_label,
                }
            )

        participation = {
            "identities": identities,
            "hasParticipation": bool(identities),
        }

    # 组装 task DTO，并按当前用户补充 joined/submittable/userDeadline/participationEligibility 等字段（部分字段为简化版）。  # noqa: E501
    task_dict = _task_to_api_model(task)

    joined = False
    joined_teams: list[dict] = []
    submittable: bool | None = None
    submittable_as_team: list[dict] = []
    user_deadline_ms: int | None = None
    participation_eligibility: dict | None = None

    if auth_user.user_id > 0:
        from app.domain.team.repositories import TeamRepository as _TeamRepo

        user_membership = await membership_service.get_user_membership(
            task_id=task_id,
            user_id=auth_user.user_id,
        )
        team_memberships = await membership_service.list_team_memberships_for_user(
            task_id=task_id,
            user_id=auth_user.user_id,
        )

        # DISAPPROVED memberships should NOT show as "joined" — otherwise the
        # frontend renders "退出赛题" button for rejected applications.
        joined = bool(
            (user_membership and user_membership.approved != 1)
            or any(m.approved != 1 for m in team_memberships)
        )
        # Hydrate joinedTeams / submittableAsTeam to Team[] (frontend type) so
        # the leave-task dialog can render team.name. Bare ids broke
        # useTaskParticipation.ts:115 (joinedTeams[0].id / .name).
        team_ids_to_load = [m.member_id for m in team_memberships]
        team_repo = _TeamRepo(session=db)
        teams_map = (
            await team_repo.get_by_ids(team_ids_to_load) if team_ids_to_load else {}
        )

        def _team_summary(team_id: int) -> dict:
            return team_summary(teams_map.get(team_id), fallback_id=team_id)

        joined_teams = [_team_summary(m.member_id) for m in team_memberships]

        is_user_approved = bool(user_membership and user_membership.approved == 0)

        if task.submitter_type == 0:  # USER
            submittable = is_user_approved
            if user_membership and user_membership.deadline:
                user_deadline_ms = int(user_membership.deadline.timestamp() * 1000)
        elif task.submitter_type == 1:  # TEAM
            approved_team_memberships = [m for m in team_memberships if m.approved == 0]
            submittable = bool(approved_team_memberships)
            submittable_as_team = [
                _team_summary(m.member_id) for m in approved_team_memberships
            ]
            if team_memberships and team_memberships[0].deadline:
                user_deadline_ms = int(team_memberships[0].deadline.timestamp() * 1000)

        if queryJoinability:
            participation_eligibility = (
                await membership_service.get_participation_eligibility(
                    task=task,
                    user_id=auth_user.user_id,
                )
            )

    # Fetch submissionSchema
    schema_repo = TaskSubmissionSchemaRepository(session=db)
    schema_entries = await schema_repo.list_by_task_id(task_id)
    task_dict["submissionSchema"] = _submission_schema_to_api(schema_entries)

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

    enriched_task = (
        await _enrich_task_models(db, [task_dict], space_id=task.space_id)
    )[0]

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
    payload: PatchTaskRequest,
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """Patch basic mutable fields of a task.

    当前实现对齐 Kotlin PatchTaskRequestDTO 的大部分字段，但暂不处理 submissionSchema。
    """
    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    from app.domain.space.repositories import SpaceAdminRelationRepository

    # is_space_admin 仍单独保留：下面「审批/驳回」只认管理员，出题者不可自审 ——
    # 那是一个比「管理员」更窄的问题，不能拿 may_teach_task 顶。
    admin_repo = SpaceAdminRelationRepository(session=db)
    is_space_admin = (
        await admin_repo.get_relation(task.space_id, auth_user.user_id) is not None
    )

    if not await may_teach_task(session=db, task=task, user_id=auth_user.user_id):
        raise ForbiddenError("Only task owner or space admin can update this task")

    # 基本字符串字段
    if payload.name is not None:
        task.name = payload.name
    if payload.intro is not None:
        task.intro = payload.intro
    if payload.description is not None:
        task.description = payload.description

    # 视频链接 — use model_fields_set to detect explicit null vs absent
    if "video_url" in payload.model_fields_set:
        task.video_url = payload.video_url if payload.video_url else None

    # 布尔开关
    if payload.resubmittable is not None:
        task.resubmittable = payload.resubmittable
    if payload.editable is not None:
        task.editable = payload.editable
    if payload.require_real_name is not None:
        task.require_real_name = payload.require_real_name

    # deadline / registrationStartAt / participantLimit 及其 hasXxx 标志
    if payload.deadline is not None:
        try:
            task.deadline = datetime.fromtimestamp(
                int(payload.deadline) / 1000.0, tz=UTC
            )
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid deadline: {exc}") from exc
    if payload.has_deadline is False:
        task.deadline = None

    if payload.registration_start_at is not None:
        try:
            task.registration_start_at = datetime.fromtimestamp(
                int(payload.registration_start_at) / 1000.0, tz=UTC
            )
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid registrationStartAt: {exc}") from exc
    if payload.has_registration_start is False:
        task.registration_start_at = None

    if payload.participant_limit is not None:
        task.participant_limit = payload.participant_limit
    if payload.has_participant_limit is False:
        task.participant_limit = None

    # 默认截止天数 / 排名
    if payload.default_deadline is not None:
        task.default_deadline = payload.default_deadline

    if payload.rank is not None:
        task.rank = payload.rank
    if payload.has_rank is False:
        task.rank = None

    # 审批状态与驳回原因 — 需要 space admin 权限，任务创建者不可自审
    if payload.approved is not None or payload.reject_reason is not None:
        if not is_space_admin:
            raise ForbiddenError("Only space admins can approve or reject tasks")
        if payload.approved is not None:
            next_approved = _map_approve_type(payload.approved)
            if next_approved == 0 and task.approved != 0:
                if await task_repo.has_prior_pending_task_for_creator(task):
                    raise BadRequestError(
                        "Creator has earlier pending tasks that must be reviewed first"
                    )
                if task.published_at is None:
                    task.published_at = datetime.now(UTC)
            task.approved = next_approved
        if payload.reject_reason is not None:
            task.reject_reason = payload.reject_reason
        # 审核痕迹只在这里写：过了上面那道门才落，通过和驳回是同一次写入，落的是
        # 审核人（不是出题人）与此刻。驳回后作者重新提交、再被审时覆盖成最新一次
        # —— 「审没审过」看 approved，这两列说的是「上一次是谁、什么时候点的」。
        task.reviewed_by = auth_user.user_id
        task.reviewed_at = datetime.now(UTC)

    if "ended_at" in payload.model_fields_set or payload.has_ended_at is not None:
        if not await may_teach_task(session=db, task=task, user_id=auth_user.user_id):
            raise ForbiddenError("Only task owner or space admin can update endedAt")
        if payload.has_ended_at is False or (
            "ended_at" in payload.model_fields_set
            and payload.ended_at is None
            and payload.has_ended_at is None
        ):
            task.ended_at = None
        elif payload.ended_at is not None:
            try:
                task.ended_at = datetime.fromtimestamp(
                    int(payload.ended_at) / 1000.0, tz=UTC
                )
            except (TypeError, ValueError) as exc:
                raise BadRequestError(f"Invalid endedAt: {exc}") from exc
        elif payload.has_ended_at is True:
            task.ended_at = datetime.now(UTC)

    # 团队大小限制，仅 TEAM 类型任务允许设置
    min_team_size: int | None = task.min_team_size
    max_team_size: int | None = task.max_team_size
    if payload.min_team_size is not None:
        min_team_size = payload.min_team_size
    if payload.max_team_size is not None:
        max_team_size = payload.max_team_size

    if task.submitter_type != 1 and (
        min_team_size is not None or max_team_size is not None
    ):
        raise BadRequestError(
            "minTeamSize and maxTeamSize can only be set for TEAM type tasks."
        )

    task.min_team_size = min_team_size
    task.max_team_size = max_team_size

    # teamLockingPolicy
    if payload.team_locking_policy is not None:
        if payload.team_locking_policy not in {"NO_LOCK", "LOCK_ON_APPROVAL"}:
            raise BadRequestError(
                f"Invalid teamLockingPolicy: {payload.team_locking_policy}"
            )
        task.team_locking_policy = payload.team_locking_policy

    # accessControlEnabled + accessDomainGroupIds
    if payload.access_control_enabled is not None:
        task.access_control_enabled = payload.access_control_enabled

    if payload.access_domain_group_ids is not None:
        domain_repo = SpaceDomainGroupDomainRepository(session=db)
        access_domain_repo = TaskAccessDomainRepository(session=db)

        if task.access_control_enabled and payload.access_domain_group_ids:
            await _ensure_domain_groups_belong_to_space(
                db=db,
                space_id=task.space_id,
                group_ids=payload.access_domain_group_ids,
            )
            groups_domains = await domain_repo.list_domains_for_groups(
                payload.access_domain_group_ids
            )
            all_domains: list[str] = []
            for gid in payload.access_domain_group_ids:
                all_domains.extend(groups_domains.get(gid, []))
            await access_domain_repo.replace_domains(
                task_id=task.id, domains=list(dict.fromkeys(all_domains))
            )
        else:
            await access_domain_repo.replace_domains(task_id=task.id, domains=[])

    # categoryId 更新：需验证归属 space 且未归档/未删除
    if payload.category_id is not None:
        space_repo = SpaceRepository(session=db)
        category_repo = SpaceCategoryRepository(session=db)
        effective_category_id = await _validate_and_get_category_id(
            space_repo=space_repo,
            category_repo=category_repo,
            space_id=task.space_id,
            category_id=payload.category_id,
        )
        task.category_id = effective_category_id

    # submissionSchema: 覆盖更新（先删后插）。
    if payload.submission_schema is not None:
        schema_repo = TaskSubmissionSchemaRepository(session=db)
        await schema_repo.replace_schema(task.id, payload.submission_schema)

    # 话题列表：简单覆盖语义，先全部软删除，再插入新集合。
    if payload.topics is not None:
        topics: list[int] = payload.topics

        # 软删除旧关系
        now = datetime.now(UTC)
        rel_stmt = select(TaskTagRelation).where(
            TaskTagRelation.task_id == task.id,
            TaskTagRelation.deleted_at.is_(None),
        )
        result = await db.execute(rel_stmt)
        existing = list(result.scalars().all())
        for rel in existing:
            rel.deleted_at = now

        # 插入新的
        for topic_id in topics:
            rel = TaskTagRelation(
                task_id=task.id,
                tag_id=topic_id,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            db.add(rel)

    task.updated_at = datetime.now(UTC)
    task = await task_repo.save(task)
    # Commit before answering. ``get_db`` commits in its teardown, which FastAPI
    # runs after the response has gone out, so a client told a task is approved
    # could open the board on its next request and not find the task there yet.
    # Same reason as the commit in ``create_task``.
    await db.commit()

    # Fetch submissionSchema for response
    schema_repo = TaskSubmissionSchemaRepository(session=db)
    schema_entries = await schema_repo.list_by_task_id(task.id)
    task_response = _task_to_api_model(task)
    task_response["submissionSchema"] = _submission_schema_to_api(schema_entries)
    task_response = (
        await _enrich_task_models(db, [task_response], space_id=task.space_id)
    )[0]

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
    sort_by: str = Query(default="publishedAt"),
    sort_order: str = Query(default="desc"),
    lifecycle: str | None = Query(default=None),
    limitedView: bool = Query(default=False),
    querySpace: bool = Query(default=False),
    queryTeam: bool = Query(default=False),
    queryJoinability: bool = Query(default=False),
    querySubmittability: bool = Query(default=False),
    queryJoined: bool = Query(default=False),
    queryUserDeadline: bool = Query(default=False),
    queryTopics: bool = Query(default=False),
    querySubmissionSchema: bool = Query(default=False),
    # 让响应里多带一个 data.distinctParticipants（这一页题目上去重后的参与人数）。
    # 默认不问：它要多跑一次本题目的报名名单查询，只有首页那种「数字要和列表一起
    # 上屏」的地方才值这一趟。
    queryDistinctParticipants: bool = Query(default=False),
    keywords: str | None = Query(default=None),
    db=Depends(get_db),
    service: TaskService = Depends(get_task_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    # 解析 sortBy / sortOrder，和 Kotlin 行为保持一致：非法值视为 400。
    if sort_by not in {
        "createdAt",
        "updatedAt",
        "deadline",
        "publishedAt",
        "reviewedAt",
    }:
        raise BadRequestError(f"Invalid sortBy: {sort_by}")
    if sort_order not in {"asc", "desc"}:
        raise BadRequestError(f"Invalid sortOrder: {sort_order}")
    if lifecycle is not None and lifecycle not in {"ended", "recruiting", "notEnded"}:
        raise BadRequestError(f"Invalid lifecycle: {lifecycle}")

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

    if approved_value == 2 and sort_by == "publishedAt" and sort_order == "desc":
        sort_by = "createdAt"
        sort_order = "asc"

    # 权限检查：查询未审批任务需要是空间管理员
    if approved_value == 2:  # NONE = 未审批
        admin_repo = SpaceAdminRelationRepository(session=db)
        relation = await admin_repo.get_relation(space, auth_user.user_id)
        if relation is None:
            raise ForbiddenError("Only space admins can view unapproved tasks")

    admin_repo = SpaceAdminRelationRepository(session=db)
    is_space_admin = await admin_repo.get_relation(space, auth_user.user_id) is not None
    viewer_email_domain = await _resolve_user_email_domain(db, auth_user.user_id)
    space_repo = SpaceRepository(session=db)
    space_entity = await space_repo.get_by_id(space)
    if space_entity is None:
        raise NotFoundError(
            "Resource space not found", data={"type": "space", "id": space}
        )
    apply_space_task_visibility = not is_space_admin or limitedView

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
        viewer_user_id=auth_user.user_id,
        viewer_email_domain=viewer_email_domain,
        viewer_is_space_admin=is_space_admin,
        apply_space_task_visibility=apply_space_task_visibility,
        visible_task_limit=space_entity.visible_task_limit,
        lifecycle=lifecycle,
        limit=pageSize,
        offset=offset,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    items = [_task_to_api_model(t) for t in tasks]
    items = await _enrich_task_models(db, items, space_id=space)
    # 卡片上那格「附件 N」要的数。跟每次列表一起给：它是卡片的一部分，不是可选的
    # 附加信息 —— 只有被点名才带的话，卡片就得为它多等一次请求。
    await _enrich_task_attachment_counts(db, items)

    # 提交表单只在被点名时才带（审核页要显示「提交要求」，见函数注释）。
    if querySubmissionSchema:
        await _enrich_task_submission_schema(db, items)

    # Topic enrichment when requested. The frontend's Task.topics is accessed
    # as `task.topics.length` so populate even when not asked (empty array)
    # so undefined-checks behave consistently.
    if queryTopics:
        await _enrich_task_topics(db, items)

    # Per-user state — joined / submittable / userDeadline / participationEligibility.
    # Only run when the frontend explicitly asks (queryJoined / querySubmittability /
    # queryJoinability / queryUserDeadline). Each flag implies the others enough
    # in practice that the cheapest correct thing is to populate them together.
    if queryJoined or querySubmittability or queryJoinability or queryUserDeadline:
        membership_service = TaskMembershipService(
            repo=TaskMembershipRepository(session=db),
            realname_repo=UserRealNameRepository(session=db),
            space_repo=SpaceRepository(session=db),
            space_rank_repo=SpaceUserRankRepository(session=db),
        )
        await _enrich_task_user_state(
            membership_service,
            list(tasks),
            items,
            user_id=auth_user.user_id,
            query_joinability=queryJoinability,
            db=db,
        )

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
        viewer_user_id=auth_user.user_id,
        viewer_email_domain=viewer_email_domain,
        viewer_is_space_admin=is_space_admin,
        apply_space_task_visibility=apply_space_task_visibility,
        visible_task_limit=space_entity.visible_task_limit,
        lifecycle=lifecycle,
    )
    returned = len(items)
    has_more = offset + returned < total
    next_start = str(offset + returned) if has_more and returned > 0 else None
    data: dict = {
        "tasks": items,
        "page": {
            "pageStart": pageStart or "",
            "pageSize": returned,
            "hasMore": has_more,
            "nextStart": next_start,
            "total": total,
        },
    }
    if queryDistinctParticipants:
        # 正是上面返回的那几道题（`items`），不是全板 —— 页面上的「领取次数」也是
        # 这几道题的和，两个数字要对得上。
        data["distinctParticipants"] = await _count_distinct_participants(
            db, space_id=space, task_ids=[item["id"] for item in items]
        )
    return {
        "code": 200,
        "message": "OK",
        "data": data,
    }


@router.delete(
    "/{taskId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Task",
)
async def delete_task(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> None:
    """Soft delete a task and its participants.

    NOTE: 与 Kotlin 版本类似，这里只做软删除；
    提交记录的删除将在后续引入 submission ORM 时一并处理。
    """
    task_repo = TaskRepository(session=db)
    membership_repo = TaskMembershipRepository(session=db)

    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    if not await may_teach_task(session=db, task=task, user_id=auth_user.user_id):
        raise ForbiddenError("Only task owner or space admin can delete this task")

    now = datetime.now(UTC)
    task.deleted_at = now

    memberships = await membership_repo.list_memberships_for_task(
        task_id=task_id, approved=None
    )
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> None:
    """Soft delete a participant by membership id."""
    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    membership = await membership_service.get_membership_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
        raise NotFoundError("Participant not found")

    # 出题者或本版管理员能撤任何报名；成员只能撤自己的。
    is_self = not membership.is_team and membership.member_id == auth_user.user_id
    if not is_self and not await may_teach_task(
        session=db, task=task, user_id=auth_user.user_id
    ):
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> None:
    """Soft delete a participant by task + member id."""
    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    membership = await membership_service.get_membership_by_task_and_member(
        task_id=task_id,
        member_id=member,
    )
    if membership is None:
        raise NotFoundError("Participant not found")

    is_self = not membership.is_team and membership.member_id == auth_user.user_id
    if not is_self and not await may_teach_task(
        session=db, task=task, user_id=auth_user.user_id
    ):
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
    payload: PatchTaskParticipantRequest,
    db=Depends(get_db),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """Patch a TaskMembership identified by (taskId, memberId) and return all participants."""  # noqa: E501
    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    if not await may_teach_task(session=db, task=task, user_id=auth_user.user_id):
        raise ForbiddenError("Only task owner or space admin can update participants")

    membership = await membership_service.get_membership_by_task_and_member(
        task_id=task_id,
        member_id=member,
    )
    if membership is None:
        raise NotFoundError("Participant not found")

    approved_value: int | None = None
    if payload.approved is not None:
        approved_value = _map_approve_type_to_int(payload.approved)

    deadline_dt: datetime | None = None
    if payload.deadline is not None:
        try:
            deadline_dt = datetime.fromtimestamp(int(payload.deadline) / 1000.0, tz=UTC)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid deadline: {exc}") from exc

    await membership_service.update_membership(
        membership=membership,
        task=task,
        approved=approved_value,
        deadline=deadline_dt,
        reject_reason=payload.reject_reason,
        email=payload.email,
        phone=payload.phone,
    )

    from app.domain.project.services import ProjectService

    await ProjectService(db).activate_participation(task=task, membership=membership)

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
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """Resubmit a previously disapproved task for approval."""
    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    # 重提审核是发布侧的动作：出题者或本版管理员都能做。
    if not await may_teach_task(session=db, task=task, user_id=auth_user.user_id):
        raise ForbiddenError(
            "Only the task creator or a board manager can resubmit "
            "the task for approval."
        )

    # 仅当当前状态为 DISAPPROVED(1) 时允许重提。
    if task.approved != 1:
        raise ForbiddenError("Task is not in a state that allows resubmission.")

    task.approved = 2  # ApproveType.NONE
    task.reject_reason = ""
    task.updated_at = datetime.now(UTC)
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    db=Depends(get_db),
) -> dict:
    """Return participants for a given task.

    NT requires @Auth("task:enumerate:participant"); we used to expose this
    publicly, leaking participant identities (member ids, contact info) to
    anyone who knew a task id.
    """
    _ = queryRealNameInfo  # 占位，后续用于控制实名信息联查

    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    if not await may_teach_task(session=db, task=task, user_id=auth_user.user_id):
        raise ForbiddenError("Only task owner or space admin can view participants")

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
    team_ids = [m.member_id for m in memberships if m.is_team]
    team_map = await TeamRepository(session=db).get_by_ids(team_ids)
    user_map: dict = {}
    profile_map: dict = {}
    if user_ids:
        user_repo = UserRepository(session=db)
        profile_repo = UserProfileRepository(session=db)
        user_map = await user_repo.get_by_ids(user_ids)
        profile_map = await profile_repo.get_profiles_by_user_ids(user_ids)

    participants = []
    for m in memberships:
        participant_info = _build_participant_user_info(
            m, user_map=user_map, profile_map=profile_map, team_map=team_map
        )
        participants.append(
            _membership_to_api_model(
                m,
                participant_info=participant_info,
                # 同上那一批 team_map，不另查一次。
                team=team_map.get(m.member_id) if m.is_team else None,
            )
        )

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
    auth_user: AuthUserInfo = Depends(require_auth_user),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    db=Depends(get_db),
) -> dict:
    """一条报名记录，判据与上面的列表版**一模一样**。

    返回体里带着报名者填的 ``email`` / ``phone``（``_membership_to_api_model``），而
    ``participantId`` 是小整数、可枚举 —— 从前这里那句 ``_ = auth_user`` 等于把报名
    表交给任何登录用户。单条是列表的一种取法，没有理由比列表更宽：看得了名单的人
    （``may_teach_task``）才看得到单条。

    403 而不是 404，口径照抄列表版：同一个调用者在同一个资源上，列表版已经用 403
    说了「你看不了这份名单」；换 404 是另一句话（「这道题上没有这个人」），而调用者
    早已知道这个人存在。
    """
    task_repo = TaskRepository(session=db)
    task = await task_repo.get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")

    if not await may_teach_task(session=db, task=task, user_id=auth_user.user_id):
        raise ForbiddenError("Only task owner or space admin can view participants")

    membership = await membership_service.get_membership_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
        raise NotFoundError("Participant not found")
    user_map: dict = {}
    profile_map: dict = {}
    team_map = {}
    if membership.is_team:
        team_map = await TeamRepository(session=db).get_by_ids([membership.member_id])
    if not membership.is_team:
        user_repo = UserRepository(session=db)
        profile_repo = UserProfileRepository(session=db)
        user_map = await user_repo.get_by_ids([membership.member_id])
        profile_map = await profile_repo.get_profiles_by_user_ids(
            [membership.member_id]
        )
    participant_info = _build_participant_user_info(
        membership, user_map=user_map, profile_map=profile_map, team_map=team_map
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "participant": _membership_to_api_model(
                membership, participant_info=participant_info
            )
        },
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    pageStart: int | None = Query(default=None, ge=0),
    pageSize: int = Query(default=20, ge=1, le=100),
    sortBy: str = Query(default="updatedAt"),
    sortOrder: str = Query(default="desc"),
    submission_service: TaskSubmissionService = Depends(get_task_submission_service),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    task_service: TaskService = Depends(get_task_service),
    team_service: TeamService = Depends(get_team_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """Enumerate submissions for a given task participant."""
    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)

    membership = await membership_service.get_membership_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
        raise NotFoundError.for_resource("participant", participant_id)

    # 出题者或本版管理员看得到这道题下任何人的提交；成员只看自己（或自己
    # 所在小队）的那一份。
    is_teacher = await may_teach_task(session=db, task=task, user_id=auth_user.user_id)
    is_own_participant = (
        membership.member_id == auth_user.user_id and not membership.is_team
    )
    is_team_member = False
    if membership.is_team:
        is_team_member = await team_service.is_team_member(
            membership.member_id, auth_user.user_id
        )
    if not is_teacher and not is_own_participant and not is_team_member:
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
    db=Depends(get_db),
    submission_service: TaskSubmissionService = Depends(get_task_submission_service),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    task_service: TaskService = Depends(get_task_service),
    team_service: TeamService = Depends(get_team_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)

    membership = await membership_service.get_membership_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
        raise NotFoundError.for_resource("participant", participant_id)

    if membership.approved != 0:
        raise ForbiddenError("Participant must be approved before submitting")
    if task.ended_at is not None:
        raise BadRequestError("Cannot submit to an ended task")

    if membership.is_team:
        is_member = await team_service.is_team_member(
            membership.member_id, auth_user.user_id
        )
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
    # Commit before answering: the page that just submitted opens the submission
    # history next, and must find what it handed in. Same reason as the commit in
    # ``create_task``.
    await db.commit()
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
        is_member = await team_service.is_team_member(
            membership.member_id, auth_user.user_id
        )
        if not is_member:
            raise ForbiddenError("Only team members can edit this submission")
    else:
        if membership.member_id != auth_user.user_id:
            raise ForbiddenError(
                "Only the participant themselves can edit their submission"
            )

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


async def _bind_review_path(
    *,
    db,
    task_id: int,
    participant_id: int,
    submission_id: int,
) -> tuple[Task, TaskMembership]:
    """Resolve what a review route addresses, binding every path id to one row.

    评审五条路由都挂在
    ``/{taskId}/participants/{participantId}/submissions/{submissionId}/review``
    之下，所以 ``submissionId`` 从来不是一个单独的主键：它只能沿着「谁提交的」
    （membership）和「提交到哪道题」（task）走到。以前把它当全局自由主键，于是
    任何一个在别的题上通过 ``may_teach_task`` 的人都能给这道题的任意提交打分、改分、
    删分 —— 拿到一个 id 就够。这里一次判完三段：membership 必须属于 path 的 task、
    submission 必须属于 path 的 participant，判据只写这一处（五条路由共用，第六个
    方法照抄这行就不会漏）。

    任何一段不成立都答 404 而不是 403：错配的 id 说明这条路径没指向任何东西，
    403 会替调用者确认「这个 submissionId 存在」。
    """
    task = await TaskRepository(session=db).get_by_id(task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)

    membership = await TaskMembershipRepository(session=db).get_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
        raise NotFoundError.for_resource("participant", participant_id)

    submission = await TaskSubmissionRepository(session=db).get_by_id(submission_id)
    if submission is None or submission.membership_id != participant_id:
        raise NotFoundError.for_resource("submission", submission_id)

    return task, membership


@router.post(
    "/{taskId}/participants/{participantId}/submissions/{submissionId}/review",
    summary="Create Submission Review",
)
async def post_task_submission_review(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    submission_id: Annotated[int, Path(ge=1, alias="submissionId")],
    payload: CreateSubmissionReviewRequest,
    review_service: TaskSubmissionReviewService = Depends(
        get_task_submission_review_service
    ),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    task, _ = await _bind_review_path(
        db=db,
        task_id=task_id,
        participant_id=participant_id,
        submission_id=submission_id,
    )
    if not await may_teach_task(session=db, task=task, user_id=auth_user.user_id):
        raise ForbiddenError("Only the author or a board manager can create review")

    existing = await review_service.get_review_dto(submission_id)
    if existing.get("reviewed"):
        raise ConflictError("Review already exists for this submission")

    review_dto = await review_service.create_review(
        submission_id=submission_id,
        accepted=payload.accepted,
        score=payload.score,
        comment=payload.comment,
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
    review_service: TaskSubmissionReviewService = Depends(
        get_task_submission_review_service
    ),
    team_service: TeamService = Depends(get_team_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """一条评审的读者，就是这条提交的读者。

    以前这里整个函数没有鉴权（只把三个参数 ``_ = (...)`` 丢掉），登录的人知道一个
    submissionId 就能读到它的成绩与评语。判据照抄同一路径上的提交列表
    ``GET .../submissions``：出题者与管理员、提交者本人、以及小队提交时的小队成员。
    """
    task, membership = await _bind_review_path(
        db=db,
        task_id=task_id,
        participant_id=participant_id,
        submission_id=submission_id,
    )

    is_teacher = await may_teach_task(session=db, task=task, user_id=auth_user.user_id)
    is_own_participant = (
        membership.member_id == auth_user.user_id and not membership.is_team
    )
    is_team_member = False
    if membership.is_team:
        is_team_member = await team_service.is_team_member(
            membership.member_id, auth_user.user_id
        )
    if not is_teacher and not is_own_participant and not is_team_member:
        raise ForbiddenError("You are not authorized to view this review")

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
    payload: PatchSubmissionReviewRequest,
    review_service: TaskSubmissionReviewService = Depends(
        get_task_submission_review_service
    ),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    task, _ = await _bind_review_path(
        db=db,
        task_id=task_id,
        participant_id=participant_id,
        submission_id=submission_id,
    )
    if not await may_teach_task(session=db, task=task, user_id=auth_user.user_id):
        raise ForbiddenError("Only the author or a board manager can update review")

    review_dto = await review_service.patch_review(
        submission_id=submission_id,
        accepted=payload.accepted,
        score=payload.score,
        comment=payload.comment,
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
    payload: CreateSubmissionReviewRequest,
    review_service: TaskSubmissionReviewService = Depends(
        get_task_submission_review_service
    ),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    task, _ = await _bind_review_path(
        db=db,
        task_id=task_id,
        participant_id=participant_id,
        submission_id=submission_id,
    )
    if not await may_teach_task(session=db, task=task, user_id=auth_user.user_id):
        raise ForbiddenError("Only the author or a board manager can update review")

    review_dto = await review_service.patch_review(
        submission_id=submission_id,
        accepted=payload.accepted,
        score=payload.score,
        comment=payload.comment,
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
    review_service: TaskSubmissionReviewService = Depends(
        get_task_submission_review_service
    ),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    task, _ = await _bind_review_path(
        db=db,
        task_id=task_id,
        participant_id=participant_id,
        submission_id=submission_id,
    )
    if not await may_teach_task(session=db, task=task, user_id=auth_user.user_id):
        raise ForbiddenError("Only the author or a board manager can delete review")

    existing = await review_service.get_review_dto(submission_id)
    if not existing.get("reviewed"):
        raise NotFoundError.for_resource("review", submission_id)

    await review_service.delete_review(submission_id=submission_id)
    review_dto = await review_service.get_review_dto(submission_id)
    return {"code": 200, "message": "OK", "data": {"review": review_dto}}


async def _ensure_task_visible_for_advice(
    *, db, task_id: int, auth_user: AuthUserInfo
) -> None:
    """A task's AI advice is about that task, so reading it takes the same
    visibility judgment as reading the task itself (``TaskVisibilityService``).

    看不到的任务，它的 advice 也看不到: the advice records quote the task and the
    conversations are the AI's reasoning about it, so a caller refused the task
    must not be handed its advice by id. Not-found rather than forbidden, for
    the same reason the task reads answer that way — a 403 would confirm the id
    names something.
    """
    task = await TaskRepository(session=db).get_by_id(task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)
    if not await TaskVisibilityService(session=db).can_view_task(
        task=task, user_id=auth_user.user_id
    ):
        raise NotFoundError.for_resource("task", task_id)
