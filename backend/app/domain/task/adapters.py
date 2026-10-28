"""本包为 `project` 声明的口子实现的那些东西。

`project` 在第 2 层，够不着第 4 层的 `task`，所以它在自己的 `ports.py` 里写下**自己
要的那几次查问**（`TaskTerms`），实现留在这里，谁在门口谁递进去 —— 和
`thread/adapters.py`、`agent_instance/adapters.py` 同一条规矩：口子按消费者分开写，
实现按提供者放。

这里只做一件事：把 `Task` / `TaskMembership` 那两行收成口子说好的内置类型。
`SubmitterType` / `ApproveType` 的序号是 `task` 自己的约定，也在这里折成一个是非，
不出家门 —— 「过审」这件事到了 `project` 那边就是一个布尔。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.ports import AcceptedTerms, Claim, StarterSource, TaskBrief
from app.domain.task import package
from app.domain.task.inputs import APPROVE_APPROVED, SUBMITTER_TEAM
from app.domain.task.models import Task, TaskMembership
from app.domain.task.teaching import protocol_for_task


def as_brief(task: Task, *, mentors: tuple[str, ...] = ()) -> TaskBrief:
    """这道赛题，收成 `project` 认得的那几栏。

    路由手上本来就有这一行，所以这里是个纯函数，不再查一次；`TaskTerms.brief` 靠
    它答同一个问题。
    """
    return TaskBrief(
        id=task.id,
        name=task.name,
        intro=task.intro,
        space_id=task.space_id,
        is_team_submission=task.submitter_type == SUBMITTER_TEAM,
        mentors=mentors,
    )


def as_claim(membership: TaskMembership) -> Claim:
    """这条报名，收成 `project` 认得的那三样。理由同 ``as_brief``。"""
    return Claim(
        member_id=membership.member_id,
        is_team=membership.is_team,
        approved=membership.approved == APPROVE_APPROVED,
    )


class TaskTerms:
    """`app.domain.project.ports.TaskTerms`：报名这条路上，`project` 要问的几次。"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def brief(self, task_id: int) -> TaskBrief | None:
        """这道赛题。已经没了给 ``None``。"""
        task = await self._session.get(Task, task_id)
        return None if task is None else as_brief(task)

    async def claim(
        self, *, task_id: int, member_id: int | None, is_team: bool
    ) -> Claim | None:
        """这个人（或小队）在这道赛题上最新的一条报名，不算删掉的那些。

        ``member_id`` 是 ``None``（项目那一行没有队伍、报名人又不是知是的用户）时
        查不到任何一条 —— 那本来就是「不是这个赛题的参与者」。
        """
        membership = await self._session.scalar(
            select(TaskMembership)
            .where(
                TaskMembership.task_id == task_id,
                TaskMembership.member_id == member_id,
                TaskMembership.is_team == is_team,
                TaskMembership.deleted_at.is_(None),
            )
            .order_by(TaskMembership.created_at.desc())
            .limit(1)
        )
        return None if membership is None else as_claim(membership)

    async def terms(self, task_id: int) -> AcceptedTerms:
        """这道赛题审过报名之后要落的条款。

        赛题那行已经没了、或者它那份协议里什么都没写，给的就是「什么都不落」的那
        一份 —— 建一个项目不该因为机构留下的赛题不在了而失败。
        """
        task = await self._session.get(Task, task_id)
        if task is None:
            return AcceptedTerms(default_role=None, compute_credits=0.0)
        protocol = await protocol_for_task(self._session, task)
        return AcceptedTerms(
            default_role=protocol.default_role,
            compute_credits=protocol.compute_credits,
            pack_prompt=protocol.pack_prompt,
        )

    async def starter(self, task_id: int) -> StarterSource | None:
        """这道题此刻的起始代码是哪一份；没有、或者题已经删了是 ``None``。"""
        task = await self._session.get(Task, task_id)
        if task is None or task.deleted_at is not None:
            return None
        row = await package.starter_file(self._session, task.id)
        if row is None:
            return None
        revision = await package.current_number(self._session, task.id) or 1
        return StarterSource(
            library_file_id=row.id,
            revision=revision,
            message=f"Starter code from task {task.name} (version {revision})",
        )
