"""tasks 路由包共用的依赖提供者、请求模型与构造辅助。

从原来的单文件 tasks.py 头部原样搬来，逐行未改。
"""

from datetime import UTC, datetime

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field

# 给了题目的「给 AI 队友的指导」(#944)：请求体沿用 项目集 PATCH 那个严格模型，
# 读写与引用校验在 app.api.task_teaching 里，接口形状在 app.api.task_serialization。
from app.api.routes.spaces import TeachingRequest
from app.api.task_teaching import (
    apply_task_teaching,
    coerce_teaching,
    validate_task_teaching,
)
from app.auth.space_access import may_publish_in_space
from app.core.errors import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
)
from app.core.storage import get_storage_backend
from app.db.session import get_db
from app.domain.attachment.services import AttachmentService
from app.domain.feature_stats import pricing
from app.domain.space.rank_service import SpaceRankService
from app.domain.space.repositories import (
    SpaceCategoryRepository,
    SpaceDomainGroupDomainRepository,
    SpaceRepository,
    SpaceUserRankRepository,
)
from app.domain.task.attachment_service import (
    TaskAttachmentService,
)
from app.domain.task.inputs import (
    map_submitter_type,
)
from app.domain.task.models import (
    Task,
    TaskTagRelation,
)
from app.domain.task.repositories import (
    TaskAccessDomainRepository,
    TaskMembershipRepository,
    TaskRepository,
    TaskSubmissionEntryRepository,
    TaskSubmissionRepository,
    TaskSubmissionReviewRepository,
    TaskSubmissionSchemaRepository,
)
from app.domain.task.services import (
    TaskMembershipService,
    TaskService,
    TaskSubmissionReviewService,
    TaskSubmissionService,
    ensure_domain_groups_belong_to_space,
    validate_and_get_category_id,
)
from app.domain.task.task_pdf_draft_service import TaskPdfDraftService
from app.domain.team.repositories import TeamRepository
from app.domain.team.services import TeamService
from app.domain.user.repositories import (
    UserRealNameRepository,
)


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
        schema_repo=TaskSubmissionSchemaRepository(session=db),
        attachments=AttachmentService.from_session(
            session=db, storage=get_storage_backend()
        ),
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


async def get_task_pdf_draft_service(db=Depends(get_db)) -> TaskPdfDraftService:
    return await TaskPdfDraftService.on_gateway(db, await pricing.model_rates())


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
    # 这道题自己的「给 AI 队友的指导」(#944)，盖过 空间/项目集 那份。省略 =
    # 不设，于是这道题沿用上层的；给了就整份替换掉上层的（见 resolve 的生效语义）。
    teaching: TeachingRequest | None = None


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
    # 这道题自己的「给 AI 队友的指导」(#944)。Sending it replaces the WHOLE
    # config — the protocol's whole-key semantics; omitting it leaves it exactly
    # as it is, so a PATCH that only renames a 题目 does not wipe the 指导.
    teaching: TeachingRequest | None = None


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


async def _create_task_entity(
    *,
    payload: dict | CreateTaskRequest,
    db,
    creator_user_id: int,
) -> Task:
    # Accept both dict (from PDF draft flow) and validated CreateTaskRequest
    if isinstance(payload, CreateTaskRequest):
        name = payload.name
        submitter_type = map_submitter_type(payload.submitter_type)
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
        teaching = coerce_teaching(payload.teaching)
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
            submitter_type = map_submitter_type(submitter_type_raw)
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

        # The PDF path carries `teaching` as free-form JSON, not a typed body.
        teaching = coerce_teaching(payload.get("teaching"))

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

    # 指导里的引用先查再建：拒了就不该留下半道题（同 项目集 PATCH 的顺序）。
    await validate_task_teaching(db, teaching=teaching, actor_user_id=creator_user_id)

    # 确认 space 存在并获取有效的 category id（传入或默认）
    effective_category_id = await validate_and_get_category_id(
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

    # 题目级「给 AI 队友的指导」(#944)：整份替换，见 apply_task_teaching。
    apply_task_teaching(task, teaching)

    # Resolve domain group IDs to actual domains and persist TaskAccessDomain records
    if access_control_enabled and access_domain_group_ids:
        await ensure_domain_groups_belong_to_space(
            session=db, space_id=space_id, group_ids=access_domain_group_ids
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


def _task_attachment_service(db) -> TaskAttachmentService:
    return TaskAttachmentService(session=db, storage=get_storage_backend())


def _attachment_service(db) -> AttachmentService:
    """还没挂到题上的文件那一层（``POST /attachments`` 用的同一个服务）。

    ``from_session`` 而不是自己造它的 repository：这里在题目这一域的地盘上，
    摸附件那一域的 repository 正是 ``test_domain_import_guard.py`` 拦的那一条。
    """
    return AttachmentService.from_session(session=db, storage=get_storage_backend())


async def _require_task(db, task_id: int) -> Task:
    task = await TaskRepository(session=db).get_by_id(task_id)
    if task is None:
        raise NotFoundError("Task not found")
    return task
