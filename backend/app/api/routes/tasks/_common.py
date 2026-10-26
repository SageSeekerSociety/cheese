"""tasks 路由包共用的请求模型与构造辅助。

从原来的单文件 tasks.py 头部搬来；依赖提供者（``get_task_service`` 那一组）后来
搬去了 ``app/api/task_deps.py`` —— 那道装配不只题目路由在用（见那个模块的说明）。
"""

from dataclasses import replace
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.api.space_payloads import TeachingRequest
from app.api.task_teaching import (
    apply_task_default_role,
    apply_task_teaching,
    coerce_teaching,
    validate_task_teaching,
)
from app.core.errors import (
    BadRequestError,
    ForbiddenError,
)
from app.domain.space import permissions as space_seats
from app.domain.space.repositories import (
    SpaceCategoryRepository,
    SpaceDomainGroupDomainRepository,
    SpaceRepository,
)
from app.domain.task import claims
from app.domain.task.inputs import (
    APPROVE_DISAPPROVED,
    START_DIRECT,
    map_start_mode,
    map_submitter_type,
)
from app.domain.task.mentors import require_consent, set_mentors
from app.domain.task.rubric import rubric_lines
from app.domain.task.services import (
    TaskService,
    ensure_domain_groups_belong_to_space,
    validate_and_get_category_id,
)
from app.domain.task.stages import new_stage_key, stage_lines, with_stage_index

# 给了题目的「给 AI 队友的指导」(#944)：请求体沿用 项目集 PATCH 那个严格模型，
# 读写与引用校验在 app.api.task_teaching 里，接口形状在 app.api.task_serialization。


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
    # How a claim starts: ``DIRECT`` (straight away; ``deadline``, when set,
    # is everyone's) or ``APPLY`` (on approval, ``defaultDeadline`` days from
    # then). A new task starts directly unless its publisher says otherwise.
    start_mode: str = Field(default="DIRECT", alias="startMode")
    deadline: int | None = None
    registration_start_at: int | None = Field(default=None, alias="registrationStartAt")
    require_real_name: bool = Field(default=False, alias="requireRealName")
    min_team_size: int | None = Field(default=None, alias="minTeamSize")
    max_team_size: int | None = Field(default=None, alias="maxTeamSize")
    rank: int | None = None
    category_id: int | None = Field(default=None, alias="categoryId")
    team_locking_policy: str = Field(default="NO_LOCK", alias="teamLockingPolicy")
    topics: list[int] = Field(default_factory=list)
    submission_schema: list[dict] = Field(
        default_factory=list, alias="submissionSchema"
    )
    # 评分要点：每行 ``{item, points, criteria}``，见 app.domain.task.rubric。
    rubric: list[dict] = Field(default_factory=list)
    rubric_public: bool = Field(default=True, alias="rubricPublic")
    # 阶段：每个 ``{name, description, dueAt}``，见 app.domain.task.stages。提交项与
    # 评分要点各用 ``stage`` 指它属于第几个；不给就是一个阶段。
    stages: list[dict] = Field(default_factory=list)
    access_control_enabled: bool = Field(default=False, alias="accessControlEnabled")
    access_domain_group_ids: list[int] = Field(
        default_factory=list, alias="accessDomainGroupIds"
    )
    # 出题时带的材料。文件先经 ``POST /attachments`` 传上来拿到 id，建题时拷进题目
    # 资料库（``package.add_uploads``）：题目还没建好时没有资料库可放。
    attachment_ids: list[int] = Field(default_factory=list, alias="attachmentIds")
    # 起始代码 (#4767)：同样先传成附件的一份 .zip，建题时查过、放进题目资料库。
    starter_attachment_id: int | None = Field(default=None, alias="starterAttachmentId")
    # 这道题自己的「给 AI 队友的指导」(#944)，盖过 空间/项目集 那份。省略 =
    # 不设，于是这道题沿用上层的；给了就整份替换掉上层的（见 resolve 的生效语义）。
    teaching: TeachingRequest | None = None
    # 这道题自己的默认队友类型名 (#944)，盖过 项目集 那份。省略 = 不设（沿用上层）；
    # 给了（含空串 = 明说「不要」）就整份替换掉这一格。
    default_role: str | None = Field(default=None, alias="defaultRole")
    # Who joins every claimer's project as a mentor (#3830): the publisher or
    # an admin of the space. Empty = nobody.
    mentors: list[str] = Field(default_factory=list)


class TaskParticipantRequest(BaseModel):
    """Body for joining a task (user or team). All fields optional."""

    model_config = ConfigDict(populate_by_name=True)

    email: str | None = None
    phone: str | None = None
    apply_reason: str | None = Field(default=None, alias="applyReason")
    personal_advantage: str | None = Field(default=None, alias="personalAdvantage")
    remark: str | None = None
    # The claimer agrees to the task's mentors joining their project (#3830).
    agree_mentors: bool = Field(default=False, alias="agreeMentors")


class JoinTaskAsTeamRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    team_id: int = Field(..., alias="teamId", gt=0)
    email: str | None = None
    phone: str | None = None
    apply_reason: str | None = Field(default=None, alias="applyReason")
    personal_advantage: str | None = Field(default=None, alias="personalAdvantage")
    remark: str | None = None
    agree_mentors: bool = Field(default=False, alias="agreeMentors")


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
    start_mode: str | None = Field(default=None, alias="startMode")
    rank: int | None = None
    has_rank: bool | None = Field(default=None, alias="hasRank")
    approved: str | None = None
    reject_reason: str | None = Field(default=None, alias="rejectReason")
    min_team_size: int | None = Field(default=None, alias="minTeamSize")
    max_team_size: int | None = Field(default=None, alias="maxTeamSize")
    team_locking_policy: str | None = Field(default=None, alias="teamLockingPolicy")
    category_id: int | None = Field(default=None, alias="categoryId")
    submission_schema: list[dict] | None = Field(default=None, alias="submissionSchema")
    # 评分要点：给了就整份替换，省略不动。
    rubric: list[dict] | None = None
    rubric_public: bool | None = Field(default=None, alias="rubricPublic")
    # 阶段：给了就整份替换，省略不动；已有的阶段带回它的 ``id``，提交跟着它走。
    stages: list[dict] | None = None
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
    # 这道题自己的默认队友类型名 (#944)。省掉 = 不动；给了（含空串）就替换这一格，
    # 与 `teaching` 同一条整键语义。
    default_role: str | None = Field(default=None, alias="defaultRole")
    # The whole list of mentors; omitted leaves it as it is.
    mentors: list[str] | None = None
    # Files the person uploaded (``POST /attachments``) to add to the 题目's
    # materials with this change: the ones 芝士 put into the changes it wrote.
    # A published 题目's own files page adds them one at a time instead.
    attachment_ids: list[int] = Field(default_factory=list, alias="attachmentIds")
    # A .zip the person uploaded the same way, to become the 题目's starter code
    # with this change (replacing the one it has); omitted leaves it as it is.
    starter_attachment_id: int | None = Field(default=None, alias="starterAttachmentId")
    # True: the 题目 keeps no starter code after this change (芝士 wrote
    # ``starter: 0``). Ignored when ``starterAttachmentId`` names a new one.
    remove_starter: bool = Field(default=False, alias="removeStarter")


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
):
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
        start_mode = map_start_mode(payload.start_mode)
        deadline_ms = payload.deadline
        registration_start_ms = payload.registration_start_at
        require_real_name = payload.require_real_name
        min_team_size = payload.min_team_size
        max_team_size = payload.max_team_size
        rank = payload.rank
        category_id = payload.category_id
        team_locking_policy = payload.team_locking_policy
        topics = payload.topics
        access_control_enabled = payload.access_control_enabled
        access_domain_group_ids = payload.access_domain_group_ids
        submission_schema = payload.submission_schema or []
        rubric_raw = payload.rubric
        rubric_public = payload.rubric_public
        stages_raw = payload.stages
        teaching = coerce_teaching(payload.teaching)
        default_role = payload.default_role
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
        start_mode = map_start_mode(str(payload.get("startMode") or START_DIRECT))

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
        default_role_raw = payload.get("defaultRole")
        default_role = default_role_raw if isinstance(default_role_raw, str) else None

        rubric_raw = payload.get("rubric") or []
        rubric_public = bool(payload.get("rubricPublic", True))
        stages_raw = payload.get("stages") or []

    # Every stage gets its id now, so the items and lines sent with it can name
    # it; one naming a stage the task will not have is refused before anything
    # is written.
    stages = [
        replace(line, key=line.key or new_stage_key())
        for line in stage_lines(
            [e for e in stages_raw if isinstance(e, dict)]
            if isinstance(stages_raw, list)
            else []
        )
    ]
    keys = [line.key for line in stages if line.key]
    submission_schema = with_stage_index(submission_schema, keys)
    rubric = rubric_lines(
        with_stage_index(rubric_raw if isinstance(rubric_raw, list) else [], keys)
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

    # 发题的门是「本板的成员」，判据在 ``app.domain.space.permissions``（``publish``）
    # —— 更早它是管理员专属（收权：那时的 ``POST /tasks`` 几乎不校验，任何登录用户
    # 拿着 space id 就能发），#1783 之后放开成任何人：发题是成员的能力，上不上板才是
    # 管理员的判断（审核走 ``PATCH /tasks/{id}``，另一条判据）。放在这里，是因为
    # ``POST /tasks`` 与草稿发布都从这里走，漏掉任一条就等于少了半道门。
    if not await space_seats.may(
        db, space_id, creator_user_id, space_seats.Permission.publish
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

    task_service = TaskService.of(db)

    task = await task_service.create_task(
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
        start_mode=start_mode,
    )

    # 题目级「给 AI 队友的指导」(#944)：整份替换，见 apply_task_teaching。
    apply_task_teaching(task, teaching)
    # 题目级默认队友 (#944)：同一份覆盖里的另一格，语义同上；省掉 = 不写。
    apply_task_default_role(task, default_role)
    task.rubric_public = rubric_public

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
            await task_service.replace_access_domains(
                task_id=task.id, domains=list(dict.fromkeys(all_domains))
            )

    # 简单设置话题关联：先不做复杂校验，仅插入关系行。
    if topics:
        await task_service.add_topic_relations(task_id=task.id, topic_ids=topics)

    # 提交表单：发布页总会带上这张表（至少一个「提交文件」项）。建题时不写，
    # 题目的提交页就一个输入项都没有，成员无处上传 —— 和 PATCH 写的是同一张表。
    if stages:
        await task_service.replace_stages(task.id, stages)
    if submission_schema:
        await task_service.replace_submission_schema(task.id, submission_schema)
    if rubric:
        await task_service.replace_rubric(task.id, rubric)

    return task


async def set_task_mentors(
    db, *, task, handles: list[str], creator_user_id: int | None
) -> None:
    """Record the task's mentors (`app.domain.task.mentors`)."""
    await set_mentors(db, task=task, handles=handles, creator_user_id=creator_user_id)


async def consent_before_a_direct_start(
    db, membership_service, *, task, member_id: int, is_team: bool, agreed: bool
) -> None:
    """A new claim on a task started directly is approved and started in the
    same request, so the mentor consent that starting asks for
    (``task_claims.start_claim``) is checked before the claim, and the place
    it takes, are written. A claim already held asks nothing here."""
    if not claims.starts_now(task):
        return
    held = await membership_service.get_membership_by_task_and_member(
        task_id=task.id, member_id=member_id, is_team=is_team
    )
    if held is None or held.approved == APPROVE_DISAPPROVED:
        await require_consent(db, task=task, agreed=agreed)
