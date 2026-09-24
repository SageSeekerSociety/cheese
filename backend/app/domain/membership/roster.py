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
from dataclasses import dataclass, replace

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import agent_instance_handle
from app.domain.identity.services import IdentityService
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
    # 这一行是不是一个 agent：判据是它背后的 user 带不带 ``AgentBinding``，从来不是
    # handle 长什么样（I9），也不是「它是不是本项目的实例」——后者会把一个有授权行
    # 但不是本项目实例的 agent-user 判成人，而房间名册对同一个 handle 答 agent，于是
    # 同一个事实又有了两份声明（I4a）。本项目的实例另外给出 ``active`` 与
    # ``project_default``，那两列只有实例答得出。
    agent: bool = False
    avatar_id: int | None = None
    # False = 已停用的队友：名册上还有它（它在已经接手的房间里照常工作），只是派新
    # 活的地方不该再列出来。人恒为 True。
    active: bool = True
    # 这一行是不是这个项目的**默认**队友，也就是一间没有 AI 席位的老房间会落到谁
    # 身上（``topic_membership/services.py._project_agent_seat`` 读的就是它）。名册
    # 上「第一个带 AI 标的」不是这个答案：那是建得最早的那一位，而停用默认队友时
    # 默认会改判给另一位，于是两者必然不同——界面照前者写名字，答话的是后者。人恒
    # 为 False。
    project_default: bool = False
    # 这一行背后没有授权行时，说明它是怎么进名册的：小队带进来的、项目的所有者、
    # 或者它是这个项目的队友。有 source 的行改不了角色也移不走。
    source: str | None = None
    team_id: int | None = None
    # The handle of that team, which the row's 「来自团队」 link goes to.
    team_handle: str | None = None
    created_at: str | None = None

    def as_dict(self) -> dict:
        """读名册的调用方拿到的那一行。

        键与字段同名，缺省的几个（``source``/``team_id``/``team_handle``/``created_at``）不出现，
        因为「没有这个字段」正是界面判断「这一行背后有没有授权行」的依据。
        """
        row: dict = {
            "handle": self.handle,
            "name": self.name,
            "role": self.role,
            "agent": self.agent,
            "avatar_id": self.avatar_id,
            "active": self.active,
            "project_default": self.project_default,
        }
        if self.source is not None:
            row["source"] = self.source
        if self.team_id is not None:
            row["team_id"] = self.team_id
        if self.team_handle is not None:
            row["team_handle"] = self.team_handle
        if self.created_at is not None:
            row["created_at"] = self.created_at
        return row


async def roster(session: AsyncSession, project_id: uuid.UUID) -> tuple[Member, ...]:
    """这个项目的名册：人在前，队友在后，每一行是一个 handle 加它的角色。

    人这一半来自 ``ProjectRepository.people``（授权行、所有者、小队带进来的人），队
    友这一半来自这个项目的 agent 实例。一个队友也可能有自己的授权行（``MemberService
    .add`` 是平台给它放座位的原语），那也仍然只是一行：授权行给出角色，实例给出名字
    和启用与否。

    ``agent`` 这一列由 binding 答（``IdentityService.agents_among``），不是由「这一行
    是不是本项目的实例」答：有授权行而实例建在别处的队友照样是 agent，房间名册对它
    答的也是 agent。

    队友那几行还带着「是不是这个项目的默认队友」：一间没有 AI 席位的老房间落到谁身
    上由它决定，而这张表是界面唯一能知道那是谁的地方。
    """
    projects = ProjectService(session)
    project = await projects.get(project_id)
    default_instance_id = (
        project.default_agent_instance_id if project is not None else None
    )
    rows = [
        Member(
            handle=person["handle"],
            name=person["name"],
            role=person["role"],
            avatar_id=person["avatar_id"],
            source=person.get("source"),
            team_id=person.get("team_id"),
            team_handle=person.get("team_handle"),
            created_at=person.get("created_at"),
        )
        for person in await projects.people(project_id)
    ]
    # 授权行那一半里也坐着 agent：``MemberService.add`` 是平台给队友放座位的原语，
    # 而队友的实例不一定建在这个项目里。谁是 agent 由 binding 答，一次问完整张表。
    agents = await IdentityService(session).agents_among([row.handle for row in rows])
    rows = [replace(row, agent=True) if row.handle in agents else row for row in rows]
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
                    project_default=instance.id == default_instance_id,
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
            project_default=instance.id == default_instance_id,
            source=held.source,
            team_id=held.team_id,
            team_handle=held.team_handle,
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
