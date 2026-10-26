"""报名：申请、个人/小队自加入、调整档位。"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.auth import ActorResolverDep
from app.api.response import ok

# 给了题目的「给 AI 队友的指导」(#944)：请求体沿用 项目集 PATCH 那个严格模型，
# 读写与引用校验在 app.api.task_teaching 里，接口形状在 app.api.task_serialization。
from app.api.routes.tasks._common import (
    JoinTaskAsTeamRequest,
    PatchTaskParticipantRequest,
    TaskParticipantRequest,
    consent_before_a_direct_start,
)
from app.api.task_claims import release_to_claim_project, start_claim
from app.api.task_deps import (
    get_task_membership_service,
)
from app.api.task_serialization import (
    _membership_to_api_model,
)
from app.api.write_access import needs
from app.auth import task_roles as task_seats
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.auth.task_roles import Permission as TaskPermission
from app.core.config import settings
from app.core.errors import (
    AuthenticationRequiredError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
)
from app.db.session import get_db
from app.domain.notification.preferences import local_time
from app.domain.space.repositories import (
    SpaceRepository,
    SpaceUserRankRepository,
)
from app.domain.task.claims import claim_of
from app.domain.task.inputs import (
    APPROVE_APPROVED,
    map_approve_type_to_int,
)
from app.domain.task.repositories import (
    TaskRepository,
)
from app.domain.task.services import (
    TaskMembershipService,
    TaskService,
)
from app.domain.task_notices import notices as task_notices
from app.domain.team.services import team_service
from app.domain.user.services import timezones_by_ids

router = APIRouter(prefix="/tasks")


@router.get("/joined", summary="Tasks the caller takes part in")
async def list_joined_tasks(resolver: ActorResolverDep, db=Depends(get_db)) -> dict:
    """The tasks the caller takes part in, across every space: what a person's
    芝士 lists when asked what is on their plate."""
    actor = await resolver.resolve()
    if not actor.authenticated or actor.user_id is None:
        raise AuthenticationRequiredError("Login required")
    tasks = await TaskService.of(db).list_joined(actor.user_id)
    # On the asker's own clock, with its offset: an agent reading a UTC
    # instant names the UTC day, which is not the day their task page shows.
    zone = (await timezones_by_ids(db, [actor.user_id])).get(actor.user_id)

    def when(moment: datetime | None) -> str | None:
        return local_time(moment, zone).isoformat() if moment else None

    rows = []
    for task in tasks:
        claim = await claim_of(db, task=task, user_id=actor.user_id)
        approved = claim is not None and claim.approved == 0
        rows.append(
            {
                "id": task.id,
                "title": task.name,
                "intro": task.intro,
                "deadline": when(task.deadline),
                # Their own deadline, set when the claim was approved; null
                # while it still waits for approval.
                "myDeadline": when(claim.deadline) if approved and claim else None,
                "approved": approved,
                "ended": task.ended_at is not None,
            }
        )
    return ok(rows)


async def _participation_response(
    db, task, membership, auth_user, *, starts: bool, agreed_mentors: bool
) -> dict:
    """The claim, and its project once the claimant has started it.

    开始 is 用这道题新建项目: a claim that is approved (a task started
    directly, at once; a task applied for, once the publisher approved it) is
    started by its claimant asking again, which makes its project, or answers
    with the one it has (继续做). ``starts`` is False for a publisher enrolling
    someone else: the project is made when that person starts. A claim still
    waiting for the publisher has no project. The claim is committed before
    the repository is made (``task_claims``).
    """
    project = None
    if starts and membership.approved == APPROVE_APPROVED:
        project = await start_claim(
            db,
            task=task,
            membership=membership,
            acting_user_id=auth_user.user_id,
            agreed_mentors=agreed_mentors,
        )
    return ok(
        {
            "participant": _membership_to_api_model(membership),
            "project": None
            if project is None
            else {
                "id": str(project.id),
                "name": project.name,
                "root_topic_id": str(project.root_topic_id),
                "team_id": project.team_id,
            },
        }
    )


@router.post(
    "/{taskId}/participants",
    summary="Apply for Task (create participant)",
    dependencies=[needs(TaskPermission.take_up, on="taskId")],
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
    is_teacher = await task_seats.teaches(db, task, auth_user.user_id)
    if member != auth_user.user_id and not is_teacher:
        raise ForbiddenError("Only task owner can add other participants")

    if task.approved != 0 and not is_teacher:
        raise BadRequestError("Cannot join a task that is not approved")
    if task.ended_at is not None:
        raise BadRequestError("Cannot join an ended task")

    # 可见性检查：禁止"看不到但能加入"

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

    is_team = task.submitter_type == 1
    if is_team and not await team_service(db).is_team_member(member, auth_user.user_id):
        # 这条路由的 ``member`` 在 TEAM 题上是**队伍 id**，而没带它时默认成了调用者
        # 自己的 **user id** —— 两张表各自自增，撞上就等于把别人的队伍报了上去。
        # 与 ``join_task_as_team`` 同一句话：要把一支队伍报上去，你先得是那支队的人。
        raise ForbiddenError(
            "You must be a member of this team to register it for a task"
        )
    # The claimant starts their own claim; a teacher enrolling a student makes
    # the claim only, and the student starts it (and agrees to the mentors).
    starts = is_team or member == auth_user.user_id
    if starts:
        await consent_before_a_direct_start(
            db,
            membership_service,
            task=task,
            member_id=member,
            is_team=is_team,
            agreed=payload.agree_mentors,
        )
    membership = await membership_service.create_membership(
        task=task,
        member_id=member,
        is_team=is_team,
        email=payload.email,
        phone=payload.phone,
        apply_reason=payload.apply_reason,
        personal_advantage=payload.personal_advantage,
        remark=payload.remark,
    )

    return await _participation_response(
        db,
        task,
        membership,
        auth_user,
        starts=starts,
        agreed_mentors=payload.agree_mentors,
    )


@router.post(
    "/{taskId}/participations/user",
    summary="Join Task as User (self-join)",
    dependencies=[needs(TaskPermission.take_up, on="taskId")],
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
            "This endpoint is for USER tasks only. Use /participations/team for team tasks."
        )

    # 可见性检查：禁止"看不到但能加入"
    await consent_before_a_direct_start(
        db,
        membership_service,
        task=task,
        member_id=auth_user.user_id,
        is_team=False,
        agreed=payload.agree_mentors,
    )

    membership = await membership_service.create_membership(
        task=task,
        member_id=auth_user.user_id,
        is_team=False,
        email=payload.email,
        phone=payload.phone,
        apply_reason=payload.apply_reason,
        personal_advantage=payload.personal_advantage,
        remark=payload.remark,
    )
    response = await _participation_response(
        db,
        task,
        membership,
        auth_user,
        starts=True,
        agreed_mentors=payload.agree_mentors,
    )
    # Commit before answering (the claim, and the project it opened). ``get_db``
    # commits in its teardown, which FastAPI runs after the response has gone
    # out, so the task owner listing the participants right after a claim could
    # find no pending record yet. Same reason as the commit in ``create_task``.
    await db.commit()
    return response


@router.post(
    "/{taskId}/participations/team",
    summary="Join Task as Team",
    dependencies=[needs(TaskPermission.take_up, on="taskId")],
)
async def join_task_as_team(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    payload: JoinTaskAsTeamRequest,
    db=Depends(get_db),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """Allow team to join a task."""

    if not await team_service(db).is_team_member(payload.team_id, auth_user.user_id):
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
            "This endpoint is for TEAM tasks only. Use /participations/user for user tasks."
        )

    # 可见性检查：禁止"看不到但能加入"
    await consent_before_a_direct_start(
        db,
        membership_service,
        task=task,
        member_id=payload.team_id,
        is_team=True,
        agreed=payload.agree_mentors,
    )

    membership = await membership_service.create_membership(
        task=task,
        member_id=payload.team_id,
        is_team=True,
        email=payload.email,
        phone=payload.phone,
        apply_reason=payload.apply_reason,
        personal_advantage=payload.personal_advantage,
        remark=payload.remark,
    )
    # Committed before answering, as in ``join_task_as_user``: the publisher
    # lists the pending claims on the next request.
    response = await _participation_response(
        db,
        task,
        membership,
        auth_user,
        starts=True,
        agreed_mentors=payload.agree_mentors,
    )
    await db.commit()
    return response


@router.patch(
    "/{taskId}/participants/{participantId}",
    summary="Update TaskMembership",
    dependencies=[needs(TaskPermission.teach, on="taskId")],
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

    membership = await membership_service.get_membership_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
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

    approved_before = membership.approved
    deadline_before = membership.deadline
    updated = await membership_service.update_membership(
        membership=membership,
        task=task,
        approved=approved_value,
        deadline=deadline_dt,
        reject_reason=payload.reject_reason,
        email=payload.email,
        phone=payload.phone,
    )

    # The claimer hears the decision, and a moved deadline on a claim already
    # approved (one being approved now gets its deadline with the decision).
    now = datetime.now(UTC)
    if updated.approved != approved_before and updated.approved in (0, 1):
        await task_notices.claim_decided(
            db,
            task=task,
            membership=updated,
            approved=updated.approved == 0,
            by=auth_user.user_id,
            at=now,
        )
    elif updated.approved == 0 and updated.deadline != deadline_before:
        await task_notices.personal_deadline_changed(
            db, task=task, membership=updated, by=auth_user.user_id, at=now
        )
    # Approval makes no project: the claimant starts it (开始做这道题). A
    # project an earlier release made while the claim waited gets the task's
    # resources now.
    if updated.approved == APPROVE_APPROVED and approved_before != APPROVE_APPROVED:
        await release_to_claim_project(db, task=task, membership=updated)
    # Commit before answering: the participant told they are approved can reload
    # the task on their next request and must see it as theirs (download and
    # submit). Same reason as the commit in ``create_task``.
    await db.commit()

    return ok(
        {
            "participant": _membership_to_api_model(updated),
        }
    )
