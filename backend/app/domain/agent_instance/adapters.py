"""本包为别的包自己声明的口子实现的那些函数。

比本包低的那些包（`membership`、`project`、`review`、`room_task`、`topic`、
`topic_membership`）够不着第 5 层，所以它们各自在自己的 `ports.py` 里写下**自己要的
那一次查问**，函数留在这里，谁在门口谁递进去 —— 和 `agent.announce`、
`delivery.agent` 里那几个同一条规矩：口子按消费者分开写，实现按提供者放。

这里只做一件事：把 `AgentInstanceService` 的答案收成那个口子说好的内置类型。
函数名跟着它实现的端口叫，一个端口一个，并排放着，好一眼看出本包被谁要走了什么。
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_instance.services import AgentInstanceService


async def task_teammate(
    session: AsyncSession,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    agent_handle: str | None,
) -> tuple[uuid.UUID, str] | None:
    """`app.domain.review.ports.TaskTeammate`：这个任务现在的 AI 队友。

    任务那一行说的是它项目里的哪个席位（``agent_handle``），落回房间、再落回项目
    自己的芝士，是 `AgentInstanceService.for_task_by_id` 的事 —— 这里只把它的答案
    拆成口子说好的那两样：署这个任务的话时用的实例 id，和认「这个队友自己干的活」
    时用的 handle。项目和房间缺一个就是 `None`。"""
    agent = await AgentInstanceService(session).for_task_by_id(
        project_id, room_id, agent_handle
    )
    if agent is None:
        return None
    return agent.instance_id, agent.handle
