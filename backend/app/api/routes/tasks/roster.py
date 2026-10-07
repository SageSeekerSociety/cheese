"""重交、查报名人、查关联小队。"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

# 给了题目的「给 AI 队友的指导」(#944)：请求体沿用 项目集 PATCH 那个严格模型，
# 读写与引用校验在 app.api.task_teaching 里，接口形状在 app.api.task_serialization。
from app.api.routes.tasks._common import (
    get_task_membership_service,
    get_task_service,
    get_team_service,
)
from app.api.task_serialization import (
    _build_participant_user_info,
    _membership_to_api_model,
    _task_to_api_model,
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
from app.domain.task.repositories import (
    TaskRepository,
)
from app.domain.task.services import (
    TaskMembershipService,
    TaskService,
)
from app.domain.team.repositories import TeamRepository
from app.domain.team.services import TeamService
from app.domain.user.repositories import (
    UserProfileRepository,
    UserRepository,
)

router = APIRouter(prefix="/tasks")


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
    avatar_map: dict = {}
    if user_ids:
        user_repo = UserRepository(session=db)
        profile_repo = UserProfileRepository(session=db)
        user_map = await user_repo.get_by_ids(user_ids)
        profile_map = await profile_repo.get_profiles_by_user_ids(user_ids)
        # 「挑过的头像」单独问：档案上的 avatar_id 每个注册路径都写死了全局默认，
        # 直接回它会让所有没挑过头像的人共用同一张脸。
        avatar_map = await profile_repo.chosen_avatar_ids(user_ids)

    participants = []
    for m in memberships:
        participant_info = _build_participant_user_info(
            m,
            user_map=user_map,
            profile_map=profile_map,
            team_map=team_map,
            avatar_map=avatar_map,
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
    avatar_map: dict = {}
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
        avatar_map = await profile_repo.chosen_avatar_ids([membership.member_id])
    participant_info = _build_participant_user_info(
        membership,
        user_map=user_map,
        profile_map=profile_map,
        team_map=team_map,
        avatar_map=avatar_map,
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
