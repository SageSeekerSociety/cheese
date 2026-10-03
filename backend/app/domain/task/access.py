"""「谁看得到、谁进得来」—— 读/加入的准入判断，以及评审路径的绑定。

从 ``services.py`` 挪出来的：它已经贴着 1500 行的上限，而这几段判断只碰题目自己
的 repository（同域，不受跨域导入闸约束）与 ``app.auth``，不依赖空间/小队仓库，
单独成篇没有跨域边。放在这里与 ``services.py`` 同域、互不循环。
"""

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.space_access import may_teach_task
from app.core.errors import ForbiddenError, NotFoundError
from app.domain.task.models import Task, TaskMembership
from app.domain.task.repositories import (
    TaskMembershipRepository,
    TaskRepository,
    TaskSubmissionRepository,
)
from app.domain.task.services import ensure_task_visible_for_ordinary_user
from app.domain.task.visibility_service import TaskVisibilityService

if TYPE_CHECKING:
    from app.domain.team.services import TeamService


async def bind_review_path(
    *,
    session: AsyncSession,
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
    task = await TaskRepository(session=session).get_by_id(task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)

    membership = await TaskMembershipRepository(session=session).get_by_id(
        participant_id
    )
    if membership is None or membership.task_id != task_id:
        raise NotFoundError.for_resource("participant", participant_id)

    submission = await TaskSubmissionRepository(session=session).get_by_id(
        submission_id
    )
    if submission is None or submission.membership_id != participant_id:
        raise NotFoundError.for_resource("submission", submission_id)

    return task, membership


async def ensure_task_joinable(
    *,
    session: AsyncSession,
    task: Task,
    user_id: int,
) -> None:
    """加入（领取 / 报名）之前那一段「你至少得看得见这道题」的判断。

    三条 join 路由（``create_task_participant``、``join_task_as_user``、
    ``join_task_as_team``）共用同一句：禁止「看不到但能加入」。用的是
    ``can_view_task``（题目没开可见范围时对任何登录用户放行），**不是**题目详情
    那三道闸 ``ensure_task_readable`` —— 后者把「未审批」也挡在外面，而这里先判
    可见、approved 由各自的调用方单独判，两条错误各说各的话。可见性不通过答 404
    「这道题不存在」（与详情同一句），随后再过 ``visibleTaskLimit`` 那道闸。
    """
    if not await TaskVisibilityService(session=session).can_view_task(
        task=task, user_id=user_id
    ):
        raise NotFoundError(
            "Resource task not found", data={"type": "task", "id": task.id}
        )
    await ensure_task_visible_for_ordinary_user(
        session=session, task=task, user_id=user_id
    )


async def ensure_can_read_participation(
    *,
    session: AsyncSession,
    task: Task,
    membership: TaskMembership,
    user_id: int,
    team_service: "TeamService",
    forbidden_message: str,
) -> None:
    """谁看得到一条报名的提交 / 评审。

    出题者或本版管理员（``may_teach_task``）、报名者本人（个人报名），以及团队
    报名时该队的成员，三者之一即可；否则用调用方给的那句话答 403。

    ``GET .../submissions`` 与 ``GET .../review`` 共用同一段判据，各自的 403
    文案由 ``forbidden_message`` 传入 —— 两处返回体逐字不变。
    """
    if await may_teach_task(session=session, task=task, user_id=user_id):
        return
    if membership.member_id == user_id and not membership.is_team:
        return
    if membership.is_team and await team_service.is_team_member(
        membership.member_id, user_id
    ):
        return
    raise ForbiddenError(forbidden_message)
