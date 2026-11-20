"""「谁看得到、谁进得来」—— 读/加入的准入判断，以及评审路径的绑定。

从 ``services.py`` 挪出来的：它已经贴着 1500 行的上限，而这几段判断只碰题目自己
的 repository（同域，不受跨域导入闸约束）与 ``app.auth``，不依赖空间/小队仓库，
单独成篇没有跨域边。放在这里与 ``services.py`` 同域、互不循环。
"""

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.domain.task.models import Task, TaskMembership
from app.domain.task.repositories import (
    TaskMembershipRepository,
    TaskRepository,
    TaskSubmissionRepository,
)

if TYPE_CHECKING:
    pass


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
    任何一个在别的题上通过 ``task_roles.teaches`` 的人都能给这道题的任意提交打分、改分、
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
