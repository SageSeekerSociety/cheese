"""题目生命周期：查询、更新、枚举、删除、按成员删改。"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy import select

# 给了题目的「给 AI 队友的指导」(#944)：请求体沿用 项目集 PATCH 那个严格模型，
# 读写与引用校验在 app.api.task_teaching 里，接口形状在 app.api.task_serialization。
from app.api.routes.tasks._common import (
    PatchTaskParticipantRequest,
    PatchTaskRequest,
    get_task_membership_service,
    get_task_service,
)
from app.api.task_serialization import (
    _enrich_task_attachment_counts,
    _enrich_task_models,
    _enrich_task_submission_schema,
    _enrich_task_topics,
    _enrich_task_user_state,
    _membership_to_api_model,
    _submission_schema_to_api,
    _task_to_api_model,
)
from app.api.task_teaching import (
    apply_task_teaching,
    coerce_teaching,
    validate_task_teaching,
)
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.auth.space_access import may_teach_task
from app.core.errors import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
)
from app.db.session import get_db
from app.domain.project.services import ProjectService
from app.domain.space.repositories import (
    SpaceAdminRelationRepository,
    SpaceCategoryRepository,
    SpaceDomainGroupDomainRepository,
    SpaceRepository,
    SpaceUserRankRepository,
)
from app.domain.task.claims import own_claim
from app.domain.task.inputs import (
    map_approve_type,
    map_approve_type_to_int,
)
from app.domain.task.models import (
    TaskTagRelation,
)
from app.domain.task.repositories import (
    TaskAccessDomainRepository,
    TaskMembershipRepository,
    TaskRepository,
    TaskSubmissionSchemaRepository,
    TopicRepository,
)
from app.domain.task.services import (
    TaskMembershipService,
    TaskService,
    count_distinct_participants,
    ensure_domain_groups_belong_to_space,
    ensure_task_readable,
    validate_and_get_category_id,
)
from app.domain.task.visibility_service import resolve_user_email_domain
from app.domain.team.repositories import TeamRepository
from app.domain.team.repositories import TeamRepository as _TeamRepo
from app.domain.team.summary import team_summary
from app.domain.user.repositories import (
    UserRealNameRepository,
)

router = APIRouter(prefix="/tasks")


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
    # 是「草稿」，对其他人来说还不该存在。三道闸都在 `ensure_task_readable` 里，
    # 题目的附属读路由（材料清单）走同一个判断。
    await ensure_task_readable(session=db, task=task, user_id=auth_user.user_id)

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
        elif task.submitter_type == 1:  # TEAM
            approved_team_memberships = [m for m in team_memberships if m.approved == 0]
            submittable = bool(approved_team_memberships)
            submittable_as_team = [
                _team_summary(m.member_id) for m in approved_team_memberships
            ]
        claim = own_claim(task, user_membership, team_memberships)
        if claim is not None and claim.deadline is not None:
            user_deadline_ms = int(claim.deadline.timestamp() * 1000)

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

    # is_space_admin 仍单独保留：下面「审批/驳回」只认管理员，出题者不可自审 ——
    # 那是一个比「管理员」更窄的问题，不能拿 may_teach_task 顶。
    admin_repo = SpaceAdminRelationRepository(session=db)
    is_space_admin = (
        await admin_repo.get_relation(task.space_id, auth_user.user_id) is not None
    )

    if not await may_teach_task(session=db, task=task, user_id=auth_user.user_id):
        raise ForbiddenError("Only task owner or space admin can update this task")

    # 「给 AI 队友的指导」(#944) 的引用先查再改：拒了就不该动这一行的任何一格
    # （同一个请求里可能还带着改名），所以拦住的位置与 项目集 PATCH 一样靠前。
    if payload.teaching is not None:
        await validate_task_teaching(
            db,
            teaching=coerce_teaching(payload.teaching),
            actor_user_id=auth_user.user_id,
        )

    # 基本字符串字段
    if payload.name is not None:
        task.name = payload.name
    if payload.intro is not None:
        task.intro = payload.intro
    if payload.description is not None:
        task.description = payload.description

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
            next_approved = map_approve_type(payload.approved)
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
            await ensure_domain_groups_belong_to_space(
                session=db,
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
        effective_category_id = await validate_and_get_category_id(
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

    # 「给 AI 队友的指导」: 整份替换这道题的那一格，见 apply_task_teaching。
    apply_task_teaching(task, coerce_teaching(payload.teaching))

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
    viewer_email_domain = await resolve_user_email_domain(db, auth_user.user_id)
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
        data["distinctParticipants"] = await count_distinct_participants(
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
        approved_value = map_approve_type_to_int(payload.approved)

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
