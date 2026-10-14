"""这个包够不着的那些事，写在这里由调用方递进来。

`topic` 在第 2 层，第 5 层那三个包（`agent`、`agent_instance`、`delivery`）里的
事都够不着，所以本包把自己要的那几次调用写在这儿，实现留在上面，谁在门口谁递进去
—— 和 `review/ports.py`、`block/ports.py` 同一条规矩：口子按消费者分开写，实现按
提供者放。

前四个问的是「这个项目的 AI 队友是谁」，实现在
`app/domain/agent_instance/adapters.py`；后两个是克隆一次会话时用的，实现就在
`agent.clone` 和 `agent.harness` 自己身上，那两个模块够得着，所以没有另起一份。

递回来的都是内置类型，没有一个是那个包里的类：本包认不出它的类型，也不该认得。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class SeatHandles(Protocol):
    """这个项目名下的 AI 队友，按席位 handle 排好：``{席位 handle: 队友的名字}``。

    私聊的角标要把每一席翻回「对面是谁」，而席位 handle 是从实例 id 派生出来的
    （`identity.handles.agent_instance_handle`）—— 那份对应关系只有 `agent_instance`
    拿得到。递回来的是两个内置类型组成的字典。

    键是席位 handle 而不是实例 id：调用方手里拿到的就是名册上那个 handle，翻回
    id 再翻回去是白跑一趟。
    """

    async def __call__(
        self, session: AsyncSession, project_id: uuid.UUID
    ) -> dict[str, str]: ...


class RoomTeammate(Protocol):
    """这个房间的队友是谁：``(它在这间房的席位 handle, 它自己的 handle)``。

    一个房间自己没有队友 —— 它坐的是人，谁答话由点名决定；答不出名字时回落到
    名册上那个席位（`agent_instance.services.for_topic` 讲的就是这个），私聊更是
    只有两席、另一席就是它。这一问是本包在 `resolve_agent` 里自己答的，而答案要
    读 ``Project``/``Topic`` 两行、算席位 handle，两样都在第 5 层。

    两样都要：开私聊要席位 handle 才能把那个人放进去，克隆会话要队友自己的
    handle 才能在它自己的会话键上接着跑。都是字符串。
    """

    async def __call__(
        self, session: AsyncSession, room_id: uuid.UUID
    ) -> tuple[str, str]: ...


class NamedTeammate(Protocol):
    """这个项目里叫 ``handle`` 的那个队友：``(席位 handle, 它显示的名字)``。

    ``handle`` 是 None 时给项目默认的那个。名字没写人话时回落到「芝士」——
    一个队友总得有个能挂出去的名字。名字不对就抛 ``NotFoundError``，不悄悄换成
    默认的那个：答成别人比说它不在这儿更糟（`agent_instance.services.for_handle`）。

    和 `RoomTeammate` 分开，是因为查法不同：那个问「这间房的队友」，这个问「这个
    项目里叫这个名字的队友，没有就用默认的」。
    """

    async def __call__(
        self, session: AsyncSession, project_id: uuid.UUID, handle: str | None
    ) -> tuple[str, str]: ...


class SeatProject(Protocol):
    """``handle`` 这个席位落在哪个项目上，不是任何一个队友的席位就是 ``None``。

    一条活认下一个队友、把它在房间里落座之前，本包要确认这一席确实是**本项目**
    的：席位 handle 是从实例 id 派生的（`identity.handles.agent_instance_handle`），
    反过来「这一席属于谁」只有 `agent_instance` 答得出来。递回来的是一个项目 id
    或者 None，本包拿它跟房间的项目比一比。

    和 `SeatHandles` 问的不是同一件事：那个是「这个项目有哪些席位」，按项目查表；
    这个是「这一席是哪个项目的」，得全表找，因为「哪儿都不是」也是一个要分开答的
    答案。
    """

    async def __call__(
        self, session: AsyncSession, handle: str
    ) -> uuid.UUID | None: ...


class ForkTranscript(Protocol):
    """把一次会话的全部状态拷成新的一次，还回那个新会话 id。

    形状收窄到本包要的那四样：源会话目录、源会话 id、目标会话目录、目标话题的
    工作目录。最后一个非得给：转写要落在**目标**那个 cwd 的 slug 下，换过去的下
    一轮 ``--resume`` 才找得到它，落在源那边就是拷了一份没人读的文件。

    源转写缺了或者空的就抛 ``FileNotFoundError`` —— 本包把它翻成一句给人看的话。

    新 id 由实现那一侧生成，本包不造：那个 id 是骨架自己的格式（今天是一个
    UUID），不是本包的事。实现在 `agent.clone`（第 5 层），本包在第 2 层。
    """

    def __call__(
        self,
        *,
        source_session_dir: Path,
        source_session_id: str,
        target_session_dir: Path,
        target_cwd: str,
    ) -> str: ...


class ProjectHarness(Protocol):
    """这个项目跑哪个骨架 —— 部署设置加项目设置答的一件（结论 28）。

    形状就是 `agent.harness.harness_for` 的形状：收项目设置，还回一个骨架名。
    克隆要把新会话记在会话键上，而会话键是 ``(会话, 队友的 handle, 骨架)``，骨架
    是其中一样；答得出它的在第 5 层。
    """

    def __call__(self, project_settings: Mapping[str, Any] | None) -> str: ...
