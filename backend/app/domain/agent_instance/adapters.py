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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_instance.own import owned_in_project
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import CHEESE_NAME, agent_instance_handle
from app.domain.membership.ports import TeammateSeat
from app.domain.project.models import Project
from app.domain.user.models import User


async def room_teammate(session: AsyncSession, room_id: uuid.UUID) -> tuple[str, str]:
    """`app.domain.topic.ports.RoomTeammate`：这个房间的队友是谁。

    席位 handle 从实例 id 派生（`identity.handles.agent_instance_handle`），队友
    自己的 handle 是它在会话键、记忆池上用的那个 —— 两样都要，谁用哪样由调用方
    定。"""
    agent = await AgentInstanceService(session).for_topic_by_id(room_id)
    return agent_instance_handle(agent.instance_id), agent.handle


async def named_teammate(
    session: AsyncSession, project_id: uuid.UUID, handle: str | None
) -> tuple[str, str]:
    """`app.domain.topic.ports.NamedTeammate`：这个项目里叫这个名字的队友。

    顺手补上它的身份行：开私聊的人下一步就把它放上名册，而名册读的是那一行
    （`AgentInstanceService.ensure_identity`，本来就是读时即补的）。名字没写人话
    时回落到「芝士」—— 一个队友总得有个能挂出去的名字。"""
    agents = AgentInstanceService(session)
    # 「这个项目里叫这个名字的队友」按 id 问，不在这里把项目行读出来：本包读第 2
    # 层的模型本就不该由这一层再添一处（`for_handle_in`）。
    agent = await agents.for_handle_in(project_id, handle)
    await agents.ensure_identity(agent)
    return agent_instance_handle(agent.id), agent.display_name or CHEESE_NAME


async def seat_handles(session: AsyncSession, project_id: uuid.UUID) -> dict[str, str]:
    """`app.domain.topic.ports.SeatHandles`：这个项目名下的席位，按席位 handle 排好。

    键是席位 handle，因为调用方手里拿的是名册上的 handle；值是队友自己的名字，
    因为它要的是「对面是谁」。"""
    rows = await AgentInstanceService(session).list_for_project(project_id)
    return {agent_instance_handle(row.id): row.handle for row in rows}


async def seat_project(session: AsyncSession, handle: str) -> uuid.UUID | None:
    """`app.domain.topic.ports.SeatProject` 和
    `app.domain.topic_membership.ports.SeatProject`：``handle`` 这一席落在哪个项目上。

    两个包各写了一份这个口子 —— `topic` 是一条活认下队友时要对一下项目，
    `topic_membership` 是落座一个人时要挡住别家的队友 —— 问的是同一件事，所以这里
    是同一个函数，名字跟两边那个端口叫。"""
    return await AgentInstanceService(session).project_of_seat(handle)


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


async def default_teammate(
    session: AsyncSession,
    project: Project,
    *,
    display_name: str | None = None,
    type_name: str | None = None,
    pack_prompt: str | None = None,
) -> None:
    """`app.domain.project.ports.DefaultTeammate`：这个项目的默认 AI 队友。

    两次调用按次序做：先播（没有才播，播种和席位都在 `materialize_default` 里，它
    自己收尾时 flush），人设只在队友**还没有**人设的时候落 —— 一个项目自己挑过的人设不该被后来的一次赛题报名覆盖。收的是那一
    行 `Project` 本身，因为播种要就地写回 ``default_agent_instance_id``、也要读
    ``root_topic_id`` 才坐得下席位。"""
    agents = AgentInstanceService(session)
    teammate = await agents.materialize_default(project, display_name=display_name)
    if teammate.type_name is None:
        if type_name:
            await agents.set_type(teammate, type_name)
            await session.flush()
        if pack_prompt:
            # The 资源包's prompt, until the contract migration after
            # b7d4e1a9c2f3 moves it into the 题目's guidance.
            await agents.append_to_persona(teammate, pack_prompt)


async def project_team(
    session: AsyncSession, project_id: uuid.UUID
) -> list[TeammateSeat]:
    """`app.domain.membership.ports.ProjectTeam`：这个项目里坐着的队友。

    Team connections determine availability; member provenance describes whose
    teammate it is. Inactive member-owned instances are omitted here. Other
    project instances retain their active state for rooms already using them.

    名字、席位 handle、默认队友这几样也在这里一次收成内置值：名册那一侧认不出
    ``AgentInstance``，也不该认得。"""
    project = await session.get(Project, project_id)
    default_instance_id = (
        project.default_agent_instance_id if project is not None else None
    )
    owners = await _own_owners(session, project_id)
    seats: list[TeammateSeat] = []
    for instance in await AgentInstanceService(session).list_for_project(project_id):
        own = owners.get(instance.id)
        if own is not None and not instance.is_active:
            continue
        seats.append(
            TeammateSeat(
                seat=agent_instance_handle(instance.id),
                session_handle=instance.handle,
                name=instance.display_name or instance.handle,
                name_source=AgentInstanceService.resolved(instance).name_source.value,
                active=instance.is_active,
                is_default=instance.id == default_instance_id,
                owner_handle=own,
            )
        )
    return seats


async def _own_owners(
    session: AsyncSession, project_id: uuid.UUID
) -> dict[uuid.UUID, str]:
    """这个项目里哪些队友是成员自己的：归谁（按 handle）。"""

    owned = await owned_in_project(session, project_id)
    if not owned:
        return {}
    rows = await session.execute(
        select(User.id, User.username).where(User.id.in_({owner for _, owner in owned}))
    )
    users: dict[int, str] = {user_id: name for user_id, name in rows.all()}
    return {instance.id: users[owner] for instance, owner in owned if owner in users}
