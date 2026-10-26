"""Writing a 题目: creating one and changing one, as the routes and the
publishing of a draft (``drafts.py``) both do it — one path, so a draft is
published exactly the way the form would have posted it."""

from datetime import UTC, datetime

from app.api.routes.tasks._common import (
    CreateTaskRequest,
    PatchTaskRequest,
    _create_task_entity,
    set_task_mentors,
)
from app.api.task_deps import _attachment_service
from app.api.task_serialization import (
    _enrich_task_models,
    _task_requirements,
    _task_to_api_model,
)
from app.api.task_teaching import (
    apply_task_default_role,
    apply_task_teaching,
    coerce_teaching,
    validate_task_teaching,
)
from app.auth import task_roles as task_seats
from app.core.errors import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
)
from app.core.sentences import say
from app.domain.space.repositories import (
    SpaceCategoryRepository,
    SpaceDomainGroupDomainRepository,
    SpaceRepository,
)
from app.domain.task import claims, package
from app.domain.task.inputs import map_approve_type, map_start_mode
from app.domain.task.rubric import rubric_lines
from app.domain.task.services import (
    TaskService,
    ensure_domain_groups_belong_to_space,
    validate_and_get_category_id,
)
from app.domain.task.stages import stage_lines, stage_news, with_stage_index
from app.domain.task.submission_state import refresh_task_claims
from app.domain.task_notices import notices as task_notices


async def create_task_from(payload: CreateTaskRequest, *, db, user_id: int) -> dict:
    """Create the 题目 ``payload`` describes, as ``user_id``; what ``POST /tasks``
    answers with."""
    task = await _create_task_entity(
        payload=payload,
        db=db,
        creator_user_id=user_id,
    )

    if payload.mentors:
        await set_task_mentors(
            db,
            task=task,
            handles=payload.mentors,
            creator_user_id=user_id,
        )

    if payload.attachment_ids:
        # 材料是随题一起发出去的，所以在这里拷进题目资料库：题目已经建好
        # （``_create_task_entity`` 里 flush 过），权限那道门也已经在同一个请求里过了
        # 一次。文件先由调用者经 ``POST /attachments`` 传上来，这里只认 id。它们是
        # 第 1 版的一部分，不另记一版。
        await package.add_uploads(
            db,
            task,
            by=user_id,
            attachment_ids=payload.attachment_ids,
            uploads=_attachment_service(db),
        )
    if payload.starter_attachment_id is not None:
        # 起始代码同理，也是第 1 版的一部分；解不开的这里就拒绝，整道题不建。
        await package.set_starter_upload(
            db,
            task,
            by=user_id,
            attachment_id=payload.starter_attachment_id,
            uploads=_attachment_service(db),
        )
    # 发布就是第 1 版，题目说明跟着写进题目资料库。材料和起始代码先放好再记：
    # 这一版记下的文件（`TaskPackageRevision.files`）要有它们。
    await package.record(db, task, by=user_id, changes={package.PUBLISHED: True})

    # 写到这里就完了 —— 题目、话题关系行、提交表单、材料都已落库，下面全是读。
    # 先提交再构造响应：``get_db`` 的提交在 ``yield`` 的退出码里，而那段跑在响应
    # 发出**之后**（FastAPI 0.137 的 ``request_stack`` 在 ``await response(...)``
    # 之后才关），不在这里提交，客户端拿到响应时这道题还没落地，紧接着来读它的
    # 请求就找不到（同 ``spaces.create_space``，合并队列 run 36296605673 实测）。
    await db.commit()

    task_model = _task_to_api_model(task, include_private_teaching=True)
    task_model = (await _enrich_task_models(db, [task_model], space_id=task.space_id))[
        0
    ]

    return {"task": task_model}


async def update_task(
    task_id: int, payload: PatchTaskRequest, *, db, service: TaskService, user_id: int
) -> dict:
    """Change the 题目 as ``payload`` says, as ``user_id``; what ``PATCH
    /tasks/{id}`` answers with."""
    task = await service.get_task(task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)

    # 「审批/驳回」只认教这块板的人，出题者不可自审 —— 比「教这道题」更窄，
    # 不能拿 ``teaches`` 顶。
    is_space_admin = await task_seats.may(
        db, task.id, user_id, task_seats.Permission.approve
    )

    if not await task_seats.teaches(db, task, user_id):
        raise ForbiddenError(say("draftTaskNotYours"))

    # 「给 AI 队友的指导」(#944) 的引用先查再改：拒了就不该动这一行的任何一格
    # （同一个请求里可能还带着改名），所以拦住的位置与 项目集 PATCH 一样靠前。
    if payload.teaching is not None:
        await validate_task_teaching(
            db,
            teaching=coerce_teaching(payload.teaching),
            actor_user_id=user_id,
        )

    # 题目内容变没变：先记下改之前的样子，和改之后比。变了的那几样记成新的一版
    # （`package.record`），提交内容或评分要点变了，领过这道题的人还会收到一条通知。
    # 发布页保存时整份带上这些，没改也一样，所以比的是内容，不是带没带。
    package_before = await _package_parts(service, task)

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
    deadline_before = task.deadline
    if payload.deadline is not None:
        try:
            task.deadline = datetime.fromtimestamp(
                int(payload.deadline) / 1000.0, tz=UTC
            )
        except (TypeError, ValueError) as exc:
            raise BadRequestError(say("taskTimeInvalid", field="deadline")) from exc
    if payload.has_deadline is False:
        task.deadline = None

    if payload.registration_start_at is not None:
        try:
            task.registration_start_at = datetime.fromtimestamp(
                int(payload.registration_start_at) / 1000.0, tz=UTC
            )
        except (TypeError, ValueError) as exc:
            raise BadRequestError(
                say("taskTimeInvalid", field="registrationStartAt")
            ) from exc
    if payload.has_registration_start is False:
        task.registration_start_at = None

    if payload.participant_limit is not None:
        task.participant_limit = payload.participant_limit
    if payload.has_participant_limit is False:
        task.participant_limit = None

    # 默认截止天数 / 排名
    if payload.default_deadline is not None:
        task.default_deadline = payload.default_deadline
    if payload.start_mode is not None:
        start_mode = map_start_mode(payload.start_mode)
        # Claims already made read their mode's rules (who sets the deadline,
        # when the project is made); once anyone holds one, the mode stays.
        # Counted under the task's row lock, which a claim being written holds
        # against: none slips in between the count and the change.
        if start_mode != task.start_mode:
            await claims.hold_against_new_claims(db, task.id)
            if await claims.has_live_claim(db, task.id):
                raise BadRequestError(say("taskStartModeLocked"))
        task.start_mode = start_mode

    if payload.rank is not None:
        task.rank = payload.rank
    if payload.has_rank is False:
        task.rank = None

    # 审批状态与驳回原因 — 需要 space admin 权限，任务创建者不可自审
    if payload.approved is not None or payload.reject_reason is not None:
        if not is_space_admin:
            raise ForbiddenError(say("taskReviewAdminsOnly"))
        if payload.approved is not None:
            next_approved = map_approve_type(payload.approved)
            if next_approved == 0 and task.approved != 0:
                if await service.has_prior_pending_task_for_creator(task):
                    raise BadRequestError(say("taskEarlierPending"))
                if task.published_at is None:
                    task.published_at = datetime.now(UTC)
            task.approved = next_approved
        if payload.reject_reason is not None:
            task.reject_reason = payload.reject_reason
        # 审核痕迹只在这里写：过了上面那道门才落，通过和驳回是同一次写入，落的是
        # 审核人（不是出题人）与此刻。驳回后作者重新提交、再被审时覆盖成最新一次
        # —— 「审没审过」看 approved，这两列说的是「上一次是谁、什么时候点的」。
        task.reviewed_by = user_id
        task.reviewed_at = datetime.now(UTC)

    if "ended_at" in payload.model_fields_set or payload.has_ended_at is not None:
        if not await task_seats.teaches(db, task, user_id):
            raise ForbiddenError(say("draftTaskNotYours"))
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
                raise BadRequestError(say("taskTimeInvalid", field="endedAt")) from exc
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
        raise BadRequestError(say("taskTeamSizeTeamOnly"))

    task.min_team_size = min_team_size
    task.max_team_size = max_team_size

    # teamLockingPolicy
    if payload.team_locking_policy is not None:
        if payload.team_locking_policy not in {"NO_LOCK", "LOCK_ON_APPROVAL"}:
            raise BadRequestError(say("taskLockingPolicyInvalid"))
        task.team_locking_policy = payload.team_locking_policy

    # accessControlEnabled + accessDomainGroupIds
    if payload.access_control_enabled is not None:
        task.access_control_enabled = payload.access_control_enabled

    if payload.access_domain_group_ids is not None:
        domain_repo = SpaceDomainGroupDomainRepository(session=db)

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
            await service.replace_access_domains(
                task_id=task.id, domains=list(dict.fromkeys(all_domains))
            )
        else:
            await service.replace_access_domains(task_id=task.id, domains=[])

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

    # 阶段先写：已交的提交、提交项和评分要点按阶段的 id 跟着它们的阶段挪到新位置，
    # 再由下面两份（若给了）整份替换 —— 那两份也按 id 指阶段，所以不会和阶段错开。
    if payload.stages is not None:
        await service.replace_stages(task.id, stage_lines(payload.stages))
    keys = await service.stage_keys(task.id)

    # submissionSchema: 覆盖更新（先删后插）。
    if payload.submission_schema is not None:
        await service.replace_submission_schema(
            task.id, with_stage_index(payload.submission_schema, keys)
        )

    # 评分要点：同样整份替换。
    if payload.rubric is not None:
        await service.replace_rubric(
            task.id, rubric_lines(with_stage_index(payload.rubric, keys))
        )
    if payload.rubric_public is not None:
        task.rubric_public = payload.rubric_public

    # 话题列表：简单覆盖语义，先全部软删除，再插入新集合。
    if payload.topics is not None:
        await service.replace_topic_relations(task_id=task.id, topic_ids=payload.topics)

    # 「给 AI 队友的指导」: 整份替换这道题的那一格，见 apply_task_teaching。
    apply_task_teaching(task, coerce_teaching(payload.teaching))
    # 「默认队友」: 同一份 `protocol_override` 里的另一格 (#944)。
    apply_task_default_role(task, payload.default_role)

    if payload.mentors is not None:
        await set_task_mentors(
            db, task=task, handles=payload.mentors, creator_user_id=task.creator_id
        )

    task.updated_at = datetime.now(UTC)
    task = await service.save(task)
    await claims.follow_the_task_deadline(db, task=task, before=deadline_before)
    package_after = await _package_parts(service, task)
    # A stage added, dropped, moved or given another due date changes what every
    # claim derives to; re-derive them in this same transaction.
    if package_before[package.STAGES] != package_after[package.STAGES]:
        await refresh_task_claims(db, task.id)
    changes: dict = {
        part: True
        for part, value in package_after.items()
        if package_before[part] != value
    }
    if payload.attachment_ids:
        # 芝士替人加进修改里的那几份文件（发布草稿时带着）：和这次的改动算一版。
        added = await package.add_uploads(
            db,
            task,
            by=user_id,
            attachment_ids=payload.attachment_ids,
            uploads=_attachment_service(db),
        )
        changes |= package.files_changed(added=added)
    if payload.starter_attachment_id is not None:
        changes[package.STARTER_CHANGE] = await package.set_starter_upload(
            db,
            task,
            by=user_id,
            attachment_id=payload.starter_attachment_id,
            uploads=_attachment_service(db),
        )
    elif payload.remove_starter and await package.starter_file(db, task.id):
        await package.remove_starter(db, task)
        changes[package.STARTER_CHANGE] = package.REMOVED
    await package.record(db, task, by=user_id, changes=changes)
    # What a taker is told about is what asks something new of them or changes
    # what they would look for: the 题目's name, the form, the rubric's lines, a
    # stage renamed, added or removed, a stage's due date. A reorder alone, or
    # the rubric shown or hidden (the editor sends that on every save), is a
    # new version of the package and no news.
    if task.approved == 0:
        await task_notices.task_changed(
            db,
            task=task,
            old_name=package_before[package.DESCRIPTION][0],
            requirements=(
                package_before[package.SUBMISSION] != package_after[package.SUBMISSION]
                or package_before[package.RUBRIC][0] != package_after[package.RUBRIC][0]
            ),
            stages=stage_news(
                package_before[package.STAGES], package_after[package.STAGES]
            ),
            by=user_id,
            at=task.updated_at,
        )
    # Commit before answering. ``get_db`` commits in its teardown, which FastAPI
    # runs after the response has gone out, so a client told a task is approved
    # could open the board on its next request and not find the task there yet.
    # Same reason as the commit in ``create_task``.
    await db.commit()

    task_response = _task_to_api_model(task, include_private_teaching=True)
    task_response.update(await _task_requirements(service, task.id))
    task_response = (
        await _enrich_task_models(db, [task_response], space_id=task.space_id)
    )[0]

    return {"task": task_response}


async def _package_parts(service: TaskService, task) -> dict:
    """题目内容里会记版的那几样现在的样子（`package` 的 `changes` 那几格）。阶段、
    提交内容和评分要点按读者看到的样子比（提交项和评分要点用阶段的 id 指阶段）。"""
    requirements = await _task_requirements(service, task.id)
    return {
        package.DESCRIPTION: (task.name, task.intro, task.description),
        package.STAGES: requirements["stages"],
        package.SUBMISSION: requirements["submissionSchema"],
        package.RUBRIC: (requirements["rubric"], task.rubric_public),
    }
