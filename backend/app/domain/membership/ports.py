"""本包够不着的那件事，写在这里由调用方递进来。

名册有两个来源（`roster.py`）：人的授权行、这个项目里的队友。前一个本包自己读得出，
后一个在第 5 层的 `agent_instance` 里（`docs/manual/dev/architecture.md` 的层表），
本包在第 2 层够不着。所以本包把这一次读写成自己的口子，实现留在那个包里
（`app/domain/agent_instance/adapters.py` 的 `project_team`），谁在门口谁递进来 ——
和 `topic/ports.py` 的 `RoomTeammate`、`project/ports.py` 的 `DefaultTeammate` 同一
条规矩：口子按消费者分开写，实现按提供者放。

递回来的行本包认得，因为是本包自己声明的（``TeammateSeat``）；交回来的**列**由那个
包筛过 —— 一个关了「成员自带 agent」的项目，成员自己的那几位不再在项目里，「启用与
否」也是那边的一行属性。名册只回答「谁在这张表上」，谁该在由这个口子背后那一位定。
"""

import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class TeammateSeat:
    """一个队友在名册上的那一行。

    ``seat`` 和 ``session_handle`` 是两个不同的 handle：前者是它**坐在名册上**、
    被别人 @ 到的那个（``identity.handles.agent_instance_handle``），后者是它自己
    在会话键和记忆池上用的那个。名册给出的是前者。
    """

    #: 席位 handle：@ 到的、通知到的、名册上写的就是它。
    seat: str
    #: 队友自己的 handle（``AgentInstance.handle``）。
    session_handle: str
    name: str
    #: ``default`` 时这个名字还是出厂那个，界面按读者语言显示（``NameSource``）。
    name_source: str
    #: 已停用的队友仍在名册上（它在接手的房间里照常工作），只是派新活的地方不该再
    #: 列出来。
    active: bool
    #: 是不是这个项目的默认队友：一间没有 AI 席位的老房间会落到谁身上（见
    #: `roster.py` 的 ``Member.project_default``）。
    is_default: bool = False
    #: 成员自己的 agent（#2991）：归谁，谁能叫它。
    owner_handle: str | None = None
    callers: str | None = None


class ProjectTeam(Protocol):
    """这个项目里坐着的队友，按出生先后排好。

    已经被这个项目的规矩筛过一遍：关了「成员自带 agent」的那些不再在列（项目把它
    们收回了），成员的 agent 停用了也一样；项目自己的队友无论如何都在，``active``
    照实说。筛在提供者那一侧，因为那条规矩的两个输入 —— 项目设置、谁是谁的 agent
    —— 都是那个包的一行。
    """

    async def __call__(
        self, session: AsyncSession, project_id: uuid.UUID
    ) -> list[TeammateSeat]: ...
