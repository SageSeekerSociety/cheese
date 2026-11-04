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
`PendingDecisions`、`ArchivedCards`、`OpenTaskCounts`、`CloseRoomTasks`、
`PlatformLine`）是本包在上面各层各自只取一两次调用的那些：支线的根消息（第 4 层）、
一间房的会话状态（第 3 层）、转写与工作目录落在哪（第 3 层）、「这间房等着谁动手」的
两个角标和归档时收卡收活（第 3、4 层）、还有一间房里有几个活还在走、以及平台在一条
对话上说一句话（第 4 层）。它们的形状大多就是提供方已有的类或函数，递进来的就是那个
对象本身，本包不另包一层。

递回来的都是内置类型：本包认不出别人家的类，也不该认得。

这里唯一读别的领域的**表**的地方不在这份名单上（`PlatformLine` 是写）：`blocks`
那几列由 `repositories/statements.py` 逐字声明，`latest.py` 写读法 —— 读的是数据，
不是那个包的代码。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.delivery_events import SettleCard
from app.domain.event_about import EventAbout
from app.domain.topic.models import Topic


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


class OpenTaskCounts(Protocol):
    """这批房间里各有几个活还在走：``{房间: 个数}``，一个都没有的房间不在字典里。

    形状就是 `room_task.services.TaskService.underway_counts`，递进来的就是那个服务
    实例。全部频道那一页的卡片上「N 个进行中的任务」是这一个数，侧栏那一行的
    `my_tasks.open` 和项目总览也是 —— 同一个判据只有 `room_task` 算得出，而它读的是
    `tasks` 表的两列（`status`、`closing_since`），这两列的意思归它解释。
    """

    async def underway_counts(
        self, room_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, int]: ...


class CloseRoomTasks(Protocol):
    """几间房关掉了，里面还没结束的活跟着结束；还回被结束的那几条（活的 id，它
    所在的房间）。一次归档要关的每一间房在一次调用里给全，而且在别的锁之前：那几
    条活的锁要一起、按同一个顺序取（`room_task.lock_order`）。

    形状就是 `room_task.archive.close_room_tasks`，实现留在那里，递进来的就是那个
    函数本身 —— 和 `ArchivedCards` 一样。一条活没有房间就没有上下文，也没有回去
    的路，所以关一个地方要把它里面的活一起关掉。

    为什么结束这件事不在这个口子里：那句话要落进每条活自己的支线，而那句话的内容
    是本包决定的（说哪条活、因为哪间房关了），递回来的那几对就是给那几句话用的；
    写下去的那一步走 `PlatformLine`。一条早就结束的活不回报，也不重写它的
    `closed_at`。
    """

    async def __call__(
        self, session: AsyncSession, room_ids: Sequence[uuid.UUID]
    ) -> Sequence[tuple[uuid.UUID, uuid.UUID]]: ...


class PlatformLine(Protocol):
    """本包要说一句平台的话（房间归档、取消归档、一间房收工），落点按 `landing()`
    那张封闭表算，写下去的是 `block`。

    形状就是 `block.adapters.platform_line`，实现留在第 4 层，递进来的就是那个函数
    本身 —— 和 `ArchivedCards`、`RoomFiles` 一样，提供方已经长成这个形状。

    为什么不一并手写这一句：写一条块带着三样本包给不出的东西 —— 轮次 id 的兜底、
    `with_keys()` 记下的内容键、落库后投给现场。少一样都不会当场报错，只会在别处
    读不出来。所以本包只决定「说什么、关于什么」，`landing()` 和真正的写入都在实现
    那一侧；这也是 `test_event_landing_guard.py` 要的形状 —— 那座守卫按模块看
    `landing()` 和 `blocks.add(kind=event)` 在不在同一处。

    ``about`` 那四档要哪几个 id，由 `landing()` 当场校验，本包把手里有的都递过去。
    """

    async def __call__(
        self,
        session: AsyncSession,
        *,
        about: EventAbout,
        project_id: uuid.UUID,
        author: str,
        content: str,
        room_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        thread_id: uuid.UUID | None = None,
    ) -> None: ...


class TopicMembership(Protocol):
    """谁在这间房里：看到哪些、加入了哪些、各有几个人、谁管得着、主人是谁。

    形状就是 `topic_membership.services.TopicMemberService` 上这五个方法，递进来的
    就是那个服务实例。「全部频道」那一页要同时问这五件事，而席位与名册在第 2 层的
    `topic_membership` 手里——和 `OpenTaskCounts` 一样，本包只认这五个方法，不认得
    它背后那批模型。

    为什么是协议而不是直接 import：`topic_membership` 反过来经 `topic` 与
    `topic_membership` 之间的那层依赖读本包，两边各 import 一次就是一个环（C3 的
    `domains-acyclic` 查的正是这个）。调用方（`api/routes/project_channels.py`）
    在门口把服务本身递进来，本包不另包一层。
    """

    async def seen(
        self, topics: list[Topic], handle: str, *, team: ProjectTeam
    ) -> list[Topic]: ...

    async def joined_topic_ids(
        self, topics: list[Topic], handle: str, *, team: ProjectTeam
    ) -> set[uuid.UUID]: ...

    async def seat_counts(
        self, topics: list[Topic], *, team: ProjectTeam
    ) -> dict[uuid.UUID, int]: ...

    async def managed_topic_ids(
        self, topics: list[Topic], actor: str
    ) -> set[uuid.UUID]: ...

    async def owner_of(self, topic_id: uuid.UUID) -> str | None: ...

    async def owners_of(
        self, topic_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, str | None]: ...
