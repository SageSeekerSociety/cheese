"""这个项目里有谁——一张名册，队友也在上面。

「谁在这个项目里」以前有两个答案：``ProjectMember``（只有人）与房间的
``topic_memberships``（人加 agent 的席位）。读名册的每一条路走的都是前一个，于是一个
agent 在项目里列不出另一个 agent——结论 12 那句「不同 handle 之间只走 chat，agent
对 agent 也是」在代码上没有输入：列不出来的东西 @ 不到，也就发不出那条 chat。

``ProjectMember`` 仍然是**人的授权行**，席位仍然是席位。它们不是两份名册，是同一张
名册的两个**来源**——所以读法只有一个，就是下面的 :func:`roster`。谁要问「这个项目
里有谁」，问它；谁要自己把两边拼一次，那就是第二份声明（I4a），而两份声明的差别只
会在某一个读者身上显出来：以前显在 agent 身上，人看不见。

角色这一列对人来说是 ``ProjectRole``（授权行上存的那个），对队友来说恒为
``member``：席位本身就是授权（概念 1.2），项目里没有「升一个队友当组长」这回事。
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import agent_instance_handle
from app.domain.project.models import ProjectRole
from app.domain.project.services import ProjectService


@dataclass(frozen=True)
class Member:
    """名册上的一行：一个 handle，加上它在这个项目里的角色。

    ``handle`` 对队友来说是它**坐在名册上**的那个 handle（``agent_instance_handle``），
    不是它记忆池的 key：@ 到的、通知到的、席位上写的都是前者，名册给出第二个名字就等
    于给出一个 @ 不动的名字。
    """

    handle: str
    name: str
    role: str
    agent: bool = False
    avatar_id: int | None = None
    # False = 已停用的队友：名册上还有它（它在已经接手的房间里照常工作），只是派新
    # 活的地方不该再列出来。人恒为 True。
    active: bool = True
    # 这一行背后没有授权行时，说明它是怎么进名册的：小队带进来的、项目的所有者、
    # 或者它是这个项目的队友。有 source 的行改不了角色也移不走。
    source: str | None = None
    team_id: int | None = None
    created_at: str | None = None

    def as_dict(self) -> dict:
        """读名册的调用方拿到的那一行。

        键与字段同名，缺省的三个（``source``/``team_id``/``created_at``）不出现，
        因为「没有这个字段」正是界面判断「这一行背后有没有授权行」的依据。
        """
        row: dict = {
            "handle": self.handle,
            "name": self.name,
            "role": self.role,
            "agent": self.agent,
            "avatar_id": self.avatar_id,
            "active": self.active,
        }
        if self.source is not None:
            row["source"] = self.source
        if self.team_id is not None:
            row["team_id"] = self.team_id
        if self.created_at is not None:
            row["created_at"] = self.created_at
        return row


async def roster(session: AsyncSession, project_id: uuid.UUID) -> tuple[Member, ...]:
    """这个项目的名册：人在前，队友在后，每一行是一个 handle 加它的角色。

    人这一半来自 ``ProjectRepository.people``（授权行、所有者、小队带进来的人），队
    友这一半来自这个项目的 agent 实例。一个队友也可能有自己的授权行（``MemberService
    .add`` 是平台给它放座位的原语），那也仍然只是一行：授权行给出角色，实例给出名字
    和启用与否。
    """
    rows = [
        Member(
            handle=person["handle"],
            name=person["name"],
            role=person["role"],
            avatar_id=person["avatar_id"],
            source=person.get("source"),
            team_id=person.get("team_id"),
            created_at=person.get("created_at"),
        )
        for person in await ProjectService(session).people(project_id)
    ]
    at = {row.handle: index for index, row in enumerate(rows)}
    for instance in await AgentInstanceService(session).list_for_project(project_id):
        seat = agent_instance_handle(instance.id)
        name = instance.display_name or instance.handle
        index = at.get(seat)
        if index is None:
            rows.append(
                Member(
                    handle=seat,
                    name=name,
                    role=ProjectRole.member.value,
                    agent=True,
                    active=instance.is_active,
                    source="agent",
                )
            )
            at[seat] = len(rows) - 1
            continue
        # 已经有授权行的队友：角色照授权行，名字和启用与否照实例——座位账号的昵称是
        # 建号时写死的常量，拿它当名字会让每个队友都叫「芝士」。
        held = rows[index]
        rows[index] = Member(
            handle=held.handle,
            name=name,
            role=held.role,
            agent=True,
            avatar_id=held.avatar_id,
            active=instance.is_active,
            source=held.source,
            team_id=held.team_id,
            created_at=held.created_at,
        )
    return tuple(rows)


async def roster_rows(session: AsyncSession, project_id: uuid.UUID) -> list[dict]:
    """:func:`roster` 的同一次读，给还按 ``list[dict]`` 传名册的那几条路。

    @ 解析、prompt 渲染、通知寻址一路传的都是行字典（``m["handle"]`` / ``m["name"]``），
    它们是**渲染**这张表，不是再读一次「项目里有谁」——所以这里只是同一次读的一个形
    状，不是第二个出处。
    """
    return [member.as_dict() for member in await roster(session, project_id)]
