"""这个包够不着的那些事，写在这里由调用方递进来。

`topic` 在第 2 层，上面每层的事都够不着，所以本包把自己要的那几次调用写在这儿，
实现留在上面，谁在门口谁递进去 —— 和 `review/ports.py`、`block/ports.py` 同一条
规矩：口子按消费者分开写，实现按提供者放。

头五个问的是「这个项目的 AI 队友是谁」，实现在
`app/domain/agent_instance/adapters.py`；`ForkTranscript` 和 `ProjectHarness` 是
克隆一次会话时用的，实现就在 `agent.clone` 和 `agent.harness` 自己身上，那两个模块
够得着，所以没有另起一份。`ProjectTeam` 是头五个里唯一本包不读的：拿到手就原样递
出去。

后半截（`RoomThread`、`SessionStore`、`WorkspacePaths`、`ReviewCards`、
`PendingDecisions`、`ArchivedCards`）是本包在第 3、4 层各自只取一两次调用的那些：
支线的根消息（第 4 层）、一间房的会话状态（第 3 层）、转写与工作目录落在哪
（第 3 层）、还有「这间房等着谁动手」的两个角标和归档时收卡（第 3、4 层）。它们的
形状大多就是提供方已有的类或函数，递进来的就是那个对象本身，本包不另包一层。

递回来的都是内置类型：本包认不出别人家的类，也不该认得。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.delivery_events import SettleCard


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


class ProjectTeam(Protocol):
    """这个项目里坐着谁，本包拿到手原样递给 `topic_membership`。

    本包从不看席位那一行长什么样：本包那五个 ``team`` 参数全部转给
    `TopicMemberService`，读那一列人的是 `membership.roster`。所以这里只写「一列
    东西」—— 和 `topic_membership/ports.py` 的同名口子一样，本包不该认得别人家的
    类，实现是同一位（`agent_instance/adapters.py` 的 ``project_team``）。

    没有去 import `membership.ports` 里那份同名口子：那会在 `topic` 与 `membership`
    之间添一条边，而 `membership` 反过来经 `topic_membership` 依赖本包，两条合起来
    就是一个环（C3 的 `domains-acyclic` 查的正是这个）。形状一样、各写一份，那条边
    就不必存在。
    """

    async def __call__(
        self, session: AsyncSession, project_id: uuid.UUID
    ) -> Sequence[object]: ...


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


class RoomThread(Protocol):
    """``conversation_id`` 是一条支线的话，这条支线的根消息是哪一条；不是就 None。

    任务从一条消息升起来时要挂在**主线那一条**下面：这条消息本身，或者它底下那条
    支线的根。哪张表答得出「这是一条支线」，不在本包手里 —— 本包在第 2 层，
    `thread` 在第 4 层（`docs/manual/dev/architecture.md` 的层表）。

    实现在 `app/domain/thread/adapters.py`，本包只认「给我一个 id 或者 None」。
    """

    async def __call__(
        self, session: AsyncSession, conversation_id: uuid.UUID
    ) -> uuid.UUID | None: ...


class SessionStore(Protocol):
    """一间房跑着的会话：接着跑的那张令牌、记下新的一张、把整间房忘掉。

    形状就是 `agent_session.services.AgentSessionService` 那三个方法，递进来的
    就是那个对象本身。--resume 的那张令牌住在这张表里，而「这一席上次跑到哪」是
    会话域的事，不是房间的事。``forget_room`` 在取消归档时用：房间的会话已经交给
    跑机器了，取消归档等于给它一个新身份，旧身份不能再被认领。
    """

    async def resume_token(
        self, conversation_id: uuid.UUID, agent_handle: str, *, harness: str
    ) -> str | None: ...

    async def remember(
        self,
        *,
        conversation_id: uuid.UUID,
        agent_handle: str,
        resume_token: str,
        harness: str,
    ) -> None: ...

    async def forget_room(self, room_id: uuid.UUID) -> None: ...


class WorkspacePaths(Protocol):
    """会话转写、沙箱工作目录落在磁盘的哪一格。

    形状就是 `app.domain.repository.service` 的两个纯函数，把那个模块递进来即可：
    它们是路径约定，不是查询，本包只是够不着那一层。

    ``sandbox_topic_workdir`` 单独要一个：转写要落在**目标** cwd 的 slug 下，换过去
    的下一轮 ``--resume`` 才找得到它。
    """

    def session_dir(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> Path: ...

    def sandbox_topic_workdir(self, topic_id: uuid.UUID) -> str: ...


class ReviewCards(Protocol):
    """这批房间里，还有哪几间点过我的名 —— ``{话题: 现在是不是等我动手}``。

    形状就是 `review.services.AcceptService.reviewer_topic_ids`，递进来的是那个
    服务实例。频道列表的「与我的相关性」要把「在等我」那盏灯点上，而验收卡是
    第 4 层的事。
    """

    async def reviewer_topic_ids(
        self, topic_ids: list[uuid.UUID], reviewer_handle: str
    ) -> dict[uuid.UUID, bool]: ...


class PendingDecisions(Protocol):
    """这批房间里，还有哪几间向我要的决策没拍板 —— 和上面同一盏灯的另外一半。

    形状就是 `notification.services.ProjectNotificationService.decision_topic_ids`。
    """

    async def decision_topic_ids(
        self, topic_ids: list[uuid.UUID], target_handle: str
    ) -> dict[uuid.UUID, bool]: ...


class ArchivedCards(Protocol):
    """一间房归档了，它上面还没结的验收卡要一起收掉。

    形状就是 `review.archive.close_cards_for_archived_topic`，实现留在那里，递进来
    的就是那个函数本身 —— 和 `agent.harness.harness_for` 一样，提供方已经长成这个
    形状，不必再包一层。

    返回值是不透明的：它答的是「改动了哪几张卡」，本包只关心收没收。
    """

    async def __call__(
        self,
        session: AsyncSession,
        *,
        topic_id: uuid.UUID,
        project_id: uuid.UUID,
        topic_title: str,
        overview_room_id: uuid.UUID,
        by: str,
        settle_card: SettleCard | None,
    ) -> object: ...
