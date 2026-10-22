"""看板上一条活/一个房间落在哪一列，以及卡面上显示哪一句话。

一句话：**显示状态从不存库，读的时候从已有事实算**，而且在后端算一次。

这条逻辑本来在前端，而且是两份 —— `lib/topicState.ts` 算房间，`lib/taskRing.ts`
算一条活，两份各写了一遍「在跑压过验收卡」这条规矩，其中一份的注释还指着另一份。
同一条规矩写两遍，就是它们迟早走散的样子；而 CLI、通知、以后任何一个客户端问的
是同一个问题，却谁也读不到那两份 TypeScript。所以答案搬到这里，前端只负责画。

## 列回答的不是「进行到哪一步」，是「**该谁动**」

这是整件事的核心，不是给现有状态换个分组：

- `building` 进行中 —— 在做，还没递出交付。
- `delivering` 检查中 —— 下一步在**平台/芝士**手上。
- `needs_you` 待处理 —— 下一步在**人**手上。
- `done` 已关闭 —— 任务关了：最后一步已采纳，或没交付就关了。
- `archived` 已归档 —— 房间才有；活不归档。

同一个客观事实会因为「谁负责下一步」落在不同列。CI 红了，平台已经派芝士去修就是
`delivering`（显示「修复检查」）；芝士推不上去、那个红没人能清掉就是 `needs_you`
（显示「检查未通过」）。同一件事，两句话，因为要动的人不是同一个。

## 每一列只能说属于自己的话，而这一条不是靠注释守的

列是**从短语推出来的**（`_show`）：每个短语是它那一列专属枚举的成员，列由成员的
类型查出来。没有任何一处代码分别挑一个列和一个短语，所以「显示了一句不属于本列
的话」在结构上没有发生的余地——不是「我们记得不要这么做」，是写不出来。

## 纯函数

没有 I/O，不碰 session，不读时钟：「现在几点」是参数。`facts_for_task` /
`facts_for_room` 和 `card_model` 读取行对象；它们也只读属性、不查库。
"""

import enum
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy.orm import Mapped

from app.core.errors import ValidationError
from app.domain.addressing import Hand
from app.domain.review_note_codes import NoteCode, NoteLevel, note_level
from app.domain.room_task.binding import catalog_id, resolve

# `Task` 是这一层唯一还**按类**标进签名的 ORM 行 —— **暂留**，不是读模型。项目 rail
# 的 Task 暂交给 card_model 和 TaskOut.model_validate：card_model 读
# 的是这条活绑了什么模型（`Task.model` / `Task.effort`），标 `Task` 才读得出来。
# `facts_for_task` 不在此列：`room_task` 已经有了自己的窄读输出
# （`repositories.ListedTask`），所以那一格走结构契约（`TaskSignals` / `TaskRow`），
# 和卡那一格（`CardSignals` / `CardRow`）一样。房间那一格也一样：`topic` 窄读出口
# 交出来的纯值走 `TopicSignals`，`Topic` 那一行走 `TopicRow` —— 各只看它被读到的
# 那几位。
from app.domain.room_task.models import Task, TaskProgress, TaskStatus
from app.domain.topic.models import TopicStatus


class CardSignals(Protocol):
    """一张卡能被折成 `CardFacts` 的五个信号 —— **窄读出口交出来的纯值**那一份契约。

    消费者递给这一层的卡有两个来源：领域内的人手上是 `AcceptCard` 那一行（ORM 行），
    HTTP 路由手上是 `review` 窄读出口交出来的纯值（`review.queries.RailCard`）。两者
    长得不一样 —— ORM 行的属性是 SQLAlchemy `Mapped` 描述符，纯值的属性是普通标量 ——
    所以在 pyright 那里是**两份**契约：这里是纯值那一份，那一行走 `CardRow`。两边都
    不必把对方那个类交出来给这一层当类型：标注一个类型就是 `room_task` 指向那个领域
    的一条边，而这一层与 `review` 之间已经有两条类型边（`review.models`、
    `review.notes`，都冻结在 `.importlinter` 里）。C3 要的是无环：再加一条，环就回
    来了，而「加一条豁免让它绿」不是解法，是把那条规矩让掉。契约式写法下，对方换成
    什么形状都行，只要这五个读得出来。

    声明成 property 而不是普通属性是有意的：pyright 对只读属性按**协变**比对，所以
    `RailCard` 的 `dict` 能满足 `Mapping[str, object]`；写成可变属性就必须一模一样。

    五个正好是 `facts_for_card` 读的那五个。多一个字段，这里就多一条「对方必须记得
    改」的绳子；少一个，那一格就折不出来。
    """

    @property
    def status(self) -> str: ...

    @property
    def note_code(self) -> NoteCode | None: ...

    @property
    def merge_state(self) -> Mapping[str, object] | None: ...

    @property
    def decided_by(self) -> str | None: ...

    @property
    def auto_merge_armed_by(self) -> str | None: ...


class CardRow(Protocol):
    """`AcceptCard` 那一行的形状 —— **ORM 行**那一份契约，按模型的原样声明 `Mapped`。

    pyright 拿调用方那行来比协议的**类成员**时，属性是 `Mapped` 描述符，普通的
    `status: str` 接不住它（`Mapped[str]` 不可赋给 `str`），所以要照模型写成
    `Mapped[...]`：读一个实例上的属性仍旧得到它裹着的那个值。同 `TeamView`
    （`domain/team/protocol.py`）、`SpaceRow`（`routes/spaces.py`）的写法。

    `status` 收成 `Mapped[Any]`：这一层只把它 `str()` 成词，而 `AcceptStatus` 这个
    枚举的名字在 `review.models` 里 —— 标进签名就是又一条 `room_task -> review.models`
    的边。`Mapped[Any]` 既接得住 `Mapped[AcceptStatus]`，也读得出值，够用。
    """

    status: Mapped[Any]
    note_code: Mapped[NoteCode | None]
    merge_state: Mapped[dict | None]
    decided_by: Mapped[str | None]
    auto_merge_armed_by: Mapped[str | None]


class TaskSignals(Protocol):
    """一条活能被折成 `TaskFacts` 的四位 —— **窄读出口交出来的纯值**那一份契约。

    和 `CardSignals` 同一条理由、同一份写法：`facts_for_task` 的两个调用方，领域内
    的人手上是 `Task` 那一行（ORM 行），HTTP 路由手上是 `room_task` 窄读出口交出来
    的纯值（`room_task.repositories.ListedTask`）——正在跑的进程和外部命令读一屏活
    读的是状态、人、日期，整行里的交付记录、环境、检查输出那些正文它们一个字不读。
    ORM 行的属性是 `Mapped` 描述符、纯值的属性是普通标量，所以在 pyright 那里是
    **两份**契约：这里是纯值那一份，那一行走 `TaskRow`。

    声明成 property 是有意的（同 `CardSignals`）：pyright 对只读属性按协变比对，
    所以 `ListedTask` 上的普通字段也接得住。

    九位正好是 `facts_for_task` 读的那九位，多一个就多一条「对方必须记得改」的
    绳子，少一个那一格就折不出来。
    """

    @property
    def status(self) -> Any: ...

    @property
    def progress_state(self) -> TaskProgress | None: ...

    @property
    def progress_set_by(self) -> str | None: ...

    @property
    def progress_run_harness(self) -> str | None: ...

    @property
    def progress_run_epoch(self) -> int | None: ...

    @property
    def progress_run_seq(self) -> int | None: ...

    @property
    def accepted_at(self) -> datetime | None: ...

    @property
    def conclusion(self) -> str | None: ...

    @property
    def closing_since(self) -> datetime | None: ...


class TaskRow(Protocol):
    """`Task` 那一行的形状 —— **ORM 行**那一份契约，按模型的原样声明 `Mapped`。

    同 `CardRow`：pyright 拿 `Task` 的类成员来比协议的类成员时，属性是 `Mapped`
    描述符，普通的 `status: str` 接不住它。`status` 同样收成 `Mapped[Any]` ——
    `TaskStatus` 的名字在 `room_task.models` 里，这一层只把它 `str()` 成词。
    """

    status: Mapped[Any]
    progress_state: Mapped[TaskProgress | None]
    progress_set_by: Mapped[str | None]
    progress_run_harness: Mapped[str | None]
    progress_run_epoch: Mapped[int | None]
    progress_run_seq: Mapped[int | None]
    accepted_at: Mapped[datetime | None]
    conclusion: Mapped[str | None]
    closing_since: Mapped[datetime | None]


class TopicSignals(Protocol):
    """一个房间**摆上清单**能被折成 `RoomFacts` 的三位 —— **纯值**那一份契约。

    和 `CardSignals` / `TaskSignals` 同一条理由、同一份写法：`facts_for_room` 的两个
    调用方，领域内的人手上是 `Topic` 那一行（ORM 行），HTTP 路由手上是 `topic` 窄读
    出口交出来的纯值（`topic.repositories.ListedRoom`）——「待我处理」把几百个频道收窄
    到真正要摆的那几个之后，读的是标题、状态、日期这几位，整行里的环境文档和简介它
    一个字不读。ORM 行的属性是 `Mapped` 描述符、纯值的属性是普通标量，所以在 pyright
    那里是**两份**契约：这里是纯值那一份，那一行走 `TopicRow`。

    声明成 property 是有意的（同 `CardSignals`）：pyright 对只读属性按协变比对，
    所以 `ListedRoom` 上的普通字段也接得住。

    三位正好是 `facts_for_room` 读的那三位，多一个就多一条「对方必须记得改」的绳子，
    少一个那一格就折不出来。
    """

    @property
    def id(self) -> uuid.UUID: ...

    @property
    def status(self) -> Any: ...

    @property
    def accepted_at(self) -> datetime | None: ...


class TopicRow(Protocol):
    """`Topic` 那一行的形状 —— **ORM 行**那一份契约，按模型的原样声明 `Mapped`。

    同 `CardRow`：pyright 拿 `Topic` 的类成员来比协议的类成员时，属性是 `Mapped`
    描述符，普通的 `status: str` 接不住它。`status` 同样收成 `Mapped[Any]`：
    `TopicStatus` 的名字在 `topic.models` 里，而这一层只读它做词比较
    （`room_presentation` 里 `facts.status == TopicStatus.archived`），不必把那一列
    的枚举类型拖进来。
    """

    id: Mapped[uuid.UUID]
    status: Mapped[Any]
    accepted_at: Mapped[datetime | None]


class Column(enum.StrEnum):
    """看板的列。判据是「该谁动」，见模块开头。"""

    building = "building"
    delivering = "delivering"
    needs_you = "needs_you"
    done = "done"
    archived = "archived"


#: 看板的列 → 下一步在谁手上。**封闭表**：`Column` 多一档而这里没跟上，查表当场抛
#: `KeyError`，而不是让新的一列悄悄落进「不通知任何人」那一档。住在这里而不是
#: `app/domain/addressing.py`：表的主键是看板的列，只有这里知道每一列是什么意思；
#: `Hand` 是那份判据（第 1 层）的词，从下面拿。
_HAND_OF_COLUMN: dict[Column, Hand] = {
    Column.building: Hand.platform,
    Column.delivering: Hand.platform,
    Column.needs_you: Hand.participant,
    Column.done: Hand.platform,
    Column.archived: Hand.platform,
}


def hand_of(column: Column) -> Hand:
    """看板上这一格的下一步在谁手上。

    看板的列回答的本来就是「该谁动」（见模块开头），所以这里不是第二次判断，是把
    同一个答案翻译成投递那一侧的词（`app/domain/addressing.py`）。
    """
    return _HAND_OF_COLUMN[column]


class Building(enum.StrEnum):
    """在做，还没递出交付。"""

    #: 房间：本轮在跑。任务：做它的那位队友的会话在跑、而它在这一次跑里没报过
    #: 状态，或者队友说它在做（`TaskProgress.working`）。
    running = "running"
    #: 任务才有：队友说它在等自己起的东西跑完——构建、CI、一条长命令。
    waiting = "waiting"
    #: 任务才有：队友还没说它在做什么，也还没递出交付。
    started = "started"
    #: 房间才有：这一轮说完了，在等下一句话。
    idle = "idle"
    #: 房间才有：还没开工。
    draft = "draft"


class Delivering(enum.StrEnum):
    """下一步在平台/芝士手上，人不用动。"""

    gate_running = "gate_running"
    awaiting_checks = "awaiting_checks"
    fixing_checks = "fixing_checks"
    #: 撞了合并冲突，芝士已经被派去解 —— `NoteCode.merge_conflict` 在写，
    #: 合并态 DIRTY 也落在这里，所以这一格是点得亮的，不是空契约。
    resolving_conflict = "resolving_conflict"
    #: 合并态 BEHIND（strict）：平台自己在 update-branch，人不用动。
    updating_branch = "updating_branch"


class NeedsYou(enum.StrEnum):
    """下一步在人手上。"""

    checks_failed = "checks_failed"
    awaiting_review = "awaiting_review"
    bounced = "bounced"
    #: 芝士提出了待确认问题，本轮停止等待回答。这一格压过队友报的状态
    #: （`task_presentation` 第 2 条）；其余几格看卡、队友的会话在不在跑和它自己报的。
    awaiting_answer = "awaiting_answer"
    #: 队友说它卡住了，没有人帮忙走不下去（`TaskProgress.blocked`）。
    blocked = "blocked"


class Done(enum.StrEnum):
    accepted = "accepted"
    #: 关闭时留下了结论：做成了，产出不是一次合并（调研、讨论出的结论）。
    completed = "completed"
    #: 关闭时什么都没留下：不做了。
    closed = "closed"


class Archived(enum.StrEnum):
    archived = "archived"


#: 短语的值是码，不是字：卡面上那句话由读者的屏幕按他选的语言画
#: （前端词条 `work.board.phrase.<码>`）。一个看板同时被说不同语言的人看，所以
#: 后端说「是哪一句」，不替任何人挑语言。
Phrase = Building | Delivering | NeedsYou | Done | Archived

#: 短语 → 它属于哪一列。**唯一**一处把两者关联起来的地方。
_COLUMN_OF: dict[type[enum.StrEnum], Column] = {
    Building: Column.building,
    Delivering: Column.delivering,
    NeedsYou: Column.needs_you,
    Done: Column.done,
    Archived: Column.archived,
}

#: 每一列允许出现的短语，给前端和测试对照用。由上面那张表推出来，不手写第二遍。
COLUMN_PHRASES: dict[Column, frozenset[str]] = {
    column: frozenset(member.value for member in phrases)
    for phrases, column in _COLUMN_OF.items()
}


@dataclass(frozen=True, slots=True)
class Presentation:
    """可以直接画出来的一格：哪一列，卡面写哪一句（短语的码）。"""

    column: Column
    phrase: str

    def as_dict(self) -> dict[str, str]:
        return {"column": str(self.column), "phrase": self.phrase}


def _show(phrase: Phrase) -> Presentation:
    """列不是挑出来的，是从短语查出来的 —— 见模块开头。"""
    return Presentation(column=_COLUMN_OF[type(phrase)], phrase=phrase.value)


# —— 事实 ——————————————————————————————————————————————————————
#
# 显式的小结构，而不是直接吃 ORM 行：这样这层测得起来（构造一行 `Task` 要先有
# 一个 session 和一堆和本题无关的必填列），也说清了它到底读了哪几个字段。


@dataclass(frozen=True, slots=True)
class Run:
    """One run of one seat's session, as the session's runner names it: the
    seat, its harness, and the ``(epoch, seq)`` of the ``running`` event that
    began the run (`SessionState.run_epoch` / `run_seq`)."""

    seat: str
    harness: str
    epoch: int
    seq: int


@dataclass(frozen=True, slots=True)
class CardFacts:
    """这条活/这个房间最新那张验收卡，窄到只剩看板要读的部分。"""

    status: str
    #: 卡停在什么上。文案在 `note` 里，判断只看码 —— `review/notes.py` 的规矩。
    note_code: NoteCode | None = None
    #: 合并态镜像（#718，`AcceptCard.merge_state`）里的 state / who —— 等采纳的
    #: 卡靠它们分列：CI 在跑是 delivering，检查红了是芝士在修，也是 delivering，
    #: CLEAN 才真的在等人。None = 还没镜像过（或这张卡不骑 PR）。
    merge_state_word: str | None = None
    merge_who: str | None = None
    #: 人已经采纳过这张卡（`decided_by`），或者布防了「绿了自动合」
    #: （`auto_merge_armed_by`）—— 「这一步交出去了」的那条线。它要和镜像合起来
    #: 读：合并队列里的 PR，GitHub 对合并态报的是 unknown，没有这一位那张卡会
    #: 掉进默认格，被说成「等你审阅」(#2046)。
    decided: bool = False


@dataclass(frozen=True, slots=True)
class TaskFacts:
    status: str
    accepted_at: datetime | None
    card: CardFacts | None
    #: 做这件活的队友最后一次说它做到哪了（`Task.progress_state`）；None = 还没说过。
    progress: TaskProgress | None = None
    #: 它是在哪个座位的哪一次跑里说的（`Task.progress_set_by` / `progress_run_*`，
    #: 那一次是它的 runner 自己说的）；
    #: None = 不是在会话里说的（平台自己写的，或者没有 runner 报会话的座位）。
    progress_run: Run | None = None
    #: 做这条活的那位队友此刻在跑的会话各在哪一次（`agent.teammate_runs`）；空集 =
    #: 在跑、但平台没记下是哪一次（那次「开始跑」在记录这一项之前就应用了）；
    #: None = 它的会话没在跑。别的 agent 在这段对话里跑，不算。
    teammate_runs: frozenset[Run] | None = None
    #: 关闭时留下过结论（`Task.conclusion`）。
    has_conclusion: bool = False
    #: 最近一条提问消息还没有回答（`BlockRepository.awaiting_an_answer`）。
    #: 回答记在提问那一块上，所以这一位不需要新增存储；但它要查一次库，所以和别的
    #: 事实一样从外面喂进来。
    awaiting_answer: bool = False
    #: 最后一次交付已经合并，AI 队友正在写总结，写完就关闭（`Task.closing_since`）。
    closing: bool = False


@dataclass(frozen=True, slots=True)
class RoomFacts:
    status: str
    #: 本轮是否在跑。房间的这一位来自跑轮次的进程本身（内存里的当下事实），不是
    #: 一列陈旧的时间戳。
    running: bool
    accepted_at: datetime | None
    card: CardFacts | None
    #: 见 `TaskFacts.awaiting_answer`，问的是房间自己那条线。
    awaiting_answer: bool = False


def facts_for_card(card: CardSignals | CardRow | None) -> CardFacts | None:
    """这张卡要读的几位，折成纯值。`None` 是「这条活上没有卡」，不是一张空卡。

    收两种形状是因为卡有两处来源：领域内的人手上是 `AcceptCard` 那一行，HTTP 路由
    手上是 `review` 窄读出口交出来的纯值 —— 一份冻结的值，没有 session 可以顺着多
    查一行。这一层只读 `status` / `note_code` / `merge_state` / `decided_by` /
    `auto_merge_armed_by` 五个属性（`CardSignals` / `CardRow`），两种形状都长得出来，
    所以折出来的 `CardFacts` 一模一样（`tests/unit/test_presentation.py` 钉住了）。
    `merge_state` 的形状也不假设：不是 `dict`（`None`、或者镜像还没写过）就当空镜像
    读，所以 `Mapping` 也收。
    """
    if card is None:
        return None
    mirror = card.merge_state if isinstance(card.merge_state, dict) else {}
    state_word = mirror.get("state")
    who = mirror.get("who")
    return CardFacts(
        status=str(card.status),
        note_code=card.note_code,
        merge_state_word=state_word if isinstance(state_word, str) else None,
        merge_who=who if isinstance(who, str) else None,
        decided=bool(card.decided_by or card.auto_merge_armed_by),
    )


def facts_for_task(
    task: TaskSignals | TaskRow,
    card: CardSignals | CardRow | None = None,
    *,
    awaiting_answer: bool = False,
    teammate_runs: frozenset[Run] | None,
) -> TaskFacts:
    """把一行活（加上它的卡）折成这层要读的事实。

    收两种形状是因为活有两处来源：领域内的人手上是 `Task` 那一行，HTTP 路由手上是
    `room_task` 窄读出口交出来的纯值 —— 一份冻结的值，没有 session 可以顺着多查
    一行。这一层只读 `status` / `progress_state` / `progress_set_by` / `progress_run_*` /
    `accepted_at` / `conclusion` / `closing_since` 九个属性 —— `TaskSignals` 和
    `TaskRow` 各声明一次这九位，两个调用点上都由 pyright 比对，所以两种形状折出来的
    `TaskFacts` 一模一样。

    ``teammate_runs`` 没有默认值：队友的会话在不在跑是这一格的事实之一，调用方不说，
    那一格就会把一条正在做的活说成「开启」。卡和房间那两对
    另有单元测试逐位对过（`tests/unit/test_presentation.py`），这一对没有；窄读那几列
    真的带得回来，押在 `room_task.repositories.listed_by_ids` 的列清单上。
    """
    return TaskFacts(
        status=str(task.status),
        accepted_at=task.accepted_at,
        card=facts_for_card(card),
        progress=task.progress_state,
        progress_run=None
        if task.progress_set_by is None
        or task.progress_run_harness is None
        or task.progress_run_epoch is None
        or task.progress_run_seq is None
        else Run(
            task.progress_set_by,
            task.progress_run_harness,
            task.progress_run_epoch,
            task.progress_run_seq,
        ),
        teammate_runs=teammate_runs,
        has_conclusion=bool(task.conclusion),
        awaiting_answer=awaiting_answer,
        closing=task.closing_since is not None,
    )


def facts_for_room(
    topic: TopicSignals | TopicRow,
    running_ids: set[uuid.UUID],
    card: CardSignals | CardRow | None = None,
    *,
    awaiting_answer: bool = False,
) -> RoomFacts:
    """把一个房间折成这层要读的事实。

    收两种形状是因为房间有两处来源：领域内的人手上是 `Topic` 那一行，HTTP 路由手上是
    `topic` 窄读出口交出来的纯值 —— 一份冻结的值，没有 session 可以顺着多查一行。这一
    层只读 `id` / `status` / `accepted_at` 三位（`TopicSignals` / `TopicRow`），两种
    形状都长得出来，所以折出来的 `RoomFacts` 一模一样（`tests/unit/test_presentation.py`
    钉住了）。
    """
    return RoomFacts(
        status=str(topic.status),
        running=topic.id in running_ids,
        accepted_at=topic.accepted_at,
        card=facts_for_card(card),
        awaiting_answer=awaiting_answer,
    )


def card_model(task: Task, *, spent: str | None, choices: dict[str, dict]) -> str:
    """卡上写哪个模型。

    **花过就写它真花的那个** —— `spent` 是 `usage` 里这条活最后一行的 `model`，
    也就是钱实际花在谁身上。一分钱还没花过的卡没有这个事实，才退回它绑的那个
    （`binding.resolve`），那是它下一轮会用的。

    这两个都不是存下来的状态：`tasks` 上没有一列记「显示什么」，也不会有。一列
    这样的状态要靠每一次真实用量去刷新它，而它对不上的那一天，卡上写着 A、账单
    上是 B，没有任何地方能说出是谁写错的。和这一层其余所有显示状态同一条规矩
    —— 读的时候从已有事实算。

    **绑坏了的照原样写出来，不在这里拒绝。** 一条活可以绑上一个项目后来用不了的
    模型 —— 项目把供给从订阅改成网关，或者运维从目录里摘掉一个型号，都会让先前
    绑上去的那批活解析不出来。这一屏是整个房间的看板，也是唯一能看见、进而改掉
    这条绑定的地方：在这里抛出去，坏掉的不是那一张卡，是这个房间的所有卡一起读
    不出来，连带把改回来的入口也关上。拒绝留在执行路径上（`binding.resolve` 自己，
    I27）。

    **花过和没花过，写出来的得是同一套词。** 目录里订阅模型的 id 是 `sonnet`，
    用量行里记的是真发出去的 `claude-sonnet-5`；两头各吐各的，同一张卡、同一个
    模型，在第一次请求之后换了个名字，用户读到的是「模型被换了」。所以 `spent`
    先过一遍目录反查（`binding.catalog_id`），认得出就写目录里那个 id。认不出来
    才照原样写：上游给回一个目录里没有的名字，写它真花在谁身上仍然比写一个猜出
    来的短名诚实。

    `spent` 和 `choices` 都从外面喂进来，和 `awaiting_answer` 一样：一个要查库，
    一个按项目算一次就够，而这一层不碰 I/O（见模块开头）。
    """
    if spent:
        return catalog_id(spent, choices) or spent
    try:
        return resolve(task, choices).model
    except ValidationError:
        return (task.model or "").strip()


# —— 卡 ————————————————————————————————————————————————————————

#: 这些状态的卡已经结算完了，不再是「此刻是什么情况」的答案。一张被驳回的卡说的
#: 是上一次递卡的下场，而这条活已经回到施工中了。
_SETTLED_CARD = frozenset(
    {"accepted", "rejected", "revoked", "gate_failed", "gate_blocked"}
)


def _is_stuck(code: NoteCode | None) -> bool:
    """这个码是不是「停住了」的那一半。

    问的是**码**，所以喂给 `note_level` 一个非空文案：它对空 note 返回 None，意思
    是「卡上这一行不用画」，不是「这个码不算停住」，而我们要的是后者 —— 一条活在
    哪一列，不该取决于有没有人把那句话写出来。

    刻意不在这里重列一遍码名：`notes.py` 已经维护着那张表，它的注释也已经记下过
    漏列的代价（`🌿 分支分叉` 和 `🚪 PR 被关` 都曾经红不起来，只是安静地被算成另
    一类）。这里再抄一份，就是让同一张表有两个会走散的副本。
    """
    return note_level(code, ".") is NoteLevel.error


def _card_presentation(card: CardFacts) -> Presentation | None:
    """这张卡此刻把这条活摆在哪一格。None = 它已经不说话了。

    先读码再读 status，因为码说的是「卡停在什么上」，比 status 具体：一张卡带着
    `repush_failed`，说的是「PR 上那个红是旧的、芝士推不上新的去清它」，
    不是「还在等 CI」。
    """
    if card.status in _SETTLED_CARD:
        return None

    # 平台已经把这个红交回给芝士去修 —— 下一步在芝士手上，别去催人。
    if card.note_code is NoteCode.checks_failed:
        return _show(Delivering.fixing_checks)
    # 撞了冲突，芝士被派去解；解完由人重试采纳，但此刻在推的是平台。
    if card.note_code is NoteCode.merge_conflict:
        return _show(Delivering.resolving_conflict)
    # 芝士的修复推不上 GitHub / 本地分支和 PR 分支分叉了：PR 上的红清不掉，而且
    # 没有任何自动的路能清掉它。这就是「CI 红了没人管」。
    if card.note_code in (NoteCode.repush_failed, NoteCode.repush_diverged):
        return _show(NeedsYou.checks_failed)
    # 其余所有「停住了」的码。刻意不再列一遍名字：`notes.py` 已经维护着那张表，
    # 而它的注释说得很清楚 —— 漏进 info 的码会和「还在等检查」长得一模一样。新增
    # 的停住码在这里自动落到「待处理」，而不是安静地被算成还在走。
    if _is_stuck(card.note_code):
        return _show(NeedsYou.bounced)

    if card.status == "pending_gate":
        return _show(Delivering.gate_running)
    if card.status == "pending":
        # 等采纳的卡按「谁的活」分列 (#718，合并态镜像)：CI 在跑 / 检查红了 /
        # 平台在换基，都不是在等人；CLEAN（或还没镜像）才真的把球放在人手上。
        #
        # 进合并队列的那张卡是这条规矩的一个例外 (#2046)：平台入队时亲手记下
        # 「已进合并队列」，而此后 GitHub 对它的合并态报的是 unknown，下一轮轮询
        # （以及浏览器读卡时补的陈旧快照）会把镜像写成 unknown/平台。那一格不该
        # 掉进默认的「等你审阅」—— 队里在跑检查，谁都不用动。note 记着这件事时就不
        # 再看镜像了：它是平台写下的入队凭据，比轮询读到的 unknown 硬。
        if card.note_code is NoteCode.waiting_merge_queue:
            return _show(Delivering.awaiting_checks)
        match card.merge_who:
            case "agent":
                if card.merge_state_word == "dirty":
                    return _show(Delivering.resolving_conflict)
                return _show(Delivering.fixing_checks)
            case "platform" if card.merge_state_word == "behind":
                return _show(Delivering.updating_branch)
            case "ci":
                return _show(Delivering.awaiting_checks)
            # 已经采纳（或布防了自动合）之后，GitHub 还没算完合并态：镜像是
            # unknown 也好、是别的没见过的词也好，这一步都不在等人手上。还没采纳
            # 的卡不适用 —— 那时 unknown 就是「读不出来」，仍旧等你去点。
            case "platform" if card.decided:
                return _show(Delivering.awaiting_checks)
            case _:
                return _show(NeedsYou.awaiting_review)
    if card.status == "conflict":
        return _show(NeedsYou.bounced)
    # 认不出来的状态不冒充答案 —— 让调用方的其余判据接着说。
    return None


def card_column(card: CardSignals | CardRow) -> Column | None:
    """这张卡此刻把它所在的地方摆在哪一列；它已经不说话了就是 None。

    和 `task_presentation` 同一个判据（`_card_presentation`），只是形状公开：调用方
    要问的是「这个地方是不是走在交付链路上」，而那条链路上的每一格都在 `Delivering`
    里（见 `Column`）。卡说不出话时返回 None，不替调用方猜一列。

    比 `task_presentation` 少几层：只看卡，不看这条活关没关、有没有在跑、在不在等
    回答。所以「说得出交付中」是充分的，反过来不成立 —— 问「有没有走在交付链路上」
    只要充分就够了。
    """
    facts = facts_for_card(card)
    if facts is None:
        return None
    shown = _card_presentation(facts)
    return None if shown is None else shown.column


def card_waits_on_reviewer(card: CardSignals | CardRow) -> bool:
    """这张卡此刻是不是真的在等验收人动手 —— 看板「待审阅」那一格。

    只是 `pending` 不够：CI 挂了、合并冲突、平台在换基、合并态还没看过，这些时候
    采纳按钮点不了，下一步在芝士或平台手上。侧栏的黄灯和「与我的相关性」都问它，
    这样两边不会一边说「等你」、一边说「芝士在修」。
    """
    facts = facts_for_card(card)
    return facts is not None and _card_presentation(facts) == _show(
        NeedsYou.awaiting_review
    )


def card_needs_agent_fix(card: CardSignals | CardRow) -> bool:
    """这张卡是不是停在「检查红了 / 冲突了 / 被退回，要 AI 去修」上。

    侧栏红灯的「检查报错没人处理」问它：卡还停在这里，这件事就还没了；检查重跑
    绿了、冲突解了、卡被撤了，它自然不再成立 —— 不靠「AI 说没说过话」去猜。
    """
    return agent_fix_kind(card) is not None


def agent_fix_kind(card: CardSignals | CardRow) -> str | None:
    """卡停在哪一种「要 AI 去修」上：`rejected`（被退回）、`gate`（闸门红 / 没跑
    成）、`conflict`（合并冲突）、`check`（检查没过）；不是这几种就 None。

    侧栏悬停按它说清楚是哪件事。
    """
    # 被验收人退回、被闸门判红 / 没跑成：卡已经结算了，但下一步明摆着是 AI 改完
    # 重递。调用方只把「这个地方最新那张」传进来，所以一旦重递了新卡就不再是它。
    status = str(card.status)
    if status == "rejected":
        return "rejected"
    if status in _BOUNCED_TO_AGENT:
        return "gate"
    facts = facts_for_card(card)
    if facts is None:
        return None
    shown = _card_presentation(facts)
    if shown == _show(Delivering.resolving_conflict):
        return "conflict"
    if shown in (_show(Delivering.fixing_checks), _show(NeedsYou.checks_failed)):
        return "check"
    return None


#: 结算了、但把球交回给 AI 的那几种卡：退回、闸门红、闸门没跑成。
_BOUNCED_TO_AGENT = frozenset({"rejected", "gate_failed", "gate_blocked"})


# —— 一条活 ————————————————————————————————————————————————————


def task_presentation(facts: TaskFacts, *, now: datetime) -> Presentation:
    """一条活此刻在哪一列、显示哪句话。

    按这个次序：

    1. **关了就是结束**：最后一次交付被采纳的写「已采纳」，其余写「已关闭」——留了
       结论的（`completed`）和什么都没留的（`closed`）码不同，字一样：关掉不等于做成。还开着的任务就算采纳过前几步，也还在做；只有最后一步
       合并后 AI 队友写总结的那一会儿例外，它已经是「已采纳」。
    2. **待回答压过队友说的**：它说过在做，可这一刻它不会自己往下走了，而看板
       显示「在做」正是让人不来看的那一句。
    3. **一条活只有一个状态，核得了的事实定它**：开启、在做、在等、卡住、等审、
       关闭。卡在等审阅（卡说在哪一步：跑检查、等人审、检查没过……）就是等审，
       验收人被点到；递卡时队友之前报的状态作废，卡在的时候它也报不了
       （`room_task.progress`）。卡被退回、作废，平台把它放回「在做」。
    4. **队友的会话在跑，而它在这一次跑里没报过，就是在做**：它从没报过（卡退回
       后也没说自己在改），或者它报的那句是上一次跑里说的——它被叫醒了：构建跑完
       了、有人回了它——那句话说的是这一次之前的事。它在这一次跑里报的照它说的：
       说那句话时它就在这一次跑里。「哪一次」是 runner 自己说的（`Run`：开始这一次
       的那个 ``running`` 事件的 epoch 与 seq，报状态的那次平台调用带着它），不是
       平台按时刻推的：runner 的事件晚到平台，也不会让这一次里报的话变旧。只看这
       条活自己那位队友的会话：审阅的、被叫进来的别的 agent 在跑，不是这条活在做。
    5. 剩下的才轮到队友自己报的：在做、在等、卡住（卡住点负责人）——「待审阅」和
       「关闭」不是它能报的，那是事实。

    `now` 收在签名里是为了和 `room_presentation` 同一个形状。
    """
    del now
    if facts.closing:
        return _show(Done.accepted)
    if facts.status == TaskStatus.closed:
        if facts.accepted_at is not None:
            return _show(Done.accepted)
        return _show(Done.completed if facts.has_conclusion else Done.closed)
    if facts.awaiting_answer:
        return _show(NeedsYou.awaiting_answer)
    if facts.card is not None:
        shown = _card_presentation(facts.card)
        if shown is not None:
            return shown
    if (
        facts.teammate_runs is not None
        and facts.progress_run not in facts.teammate_runs
    ):
        return _show(Building.running)
    if facts.progress is TaskProgress.blocked:
        return _show(NeedsYou.blocked)
    if facts.progress is TaskProgress.working:
        return _show(Building.running)
    if facts.progress is TaskProgress.waiting:
        return _show(Building.waiting)
    # 退回、闸门判红：卡结算了，球交回给芝士去改。队友没说它在改、会话也没在跑
    # （上面已经答过），也没有新卡递上来，这件事就停在了负责人手上 —— 侧栏把它标成没人处理，这里不能
    # 还说「进行中」。
    if facts.card is not None and facts.card.status in _BOUNCED_TO_AGENT:
        return _show(NeedsYou.bounced)
    return _show(Building.started)


#: 负责人自己要动手的那几格（「需要我处理」的负责人那一条）：检查红了芝士清不掉，
#: 被退回之后没人接着改，队友说它卡住了。只是任务开着、在等别人审阅或回答，都不算
#: —— 那是别人手上的事。
_OWNER_ACTS_ON = frozenset(
    {
        NeedsYou.checks_failed.value,
        NeedsYou.bounced.value,
        NeedsYou.blocked.value,
    }
)


def owner_acts_on(shown: Presentation) -> bool:
    """这一格的下一步是不是在负责人手上。"""
    return shown.phrase in _OWNER_ACTS_ON


def waiting_on(
    shown: Presentation,
    *,
    reviewer: str | None,
    asked: str | None,
) -> str | None:
    """这一格在等哪一个人：卡面上写「待 某某 审阅」「待 某某 回答」的那个某某。
    不是在等一个具体的人就是 None —— 一道题问了几个人、问了所有人，就不是。"""
    if shown.phrase == NeedsYou.awaiting_review.value:
        return reviewer
    if shown.phrase == NeedsYou.awaiting_answer.value:
        return asked
    return None


# —— 停滞 ——————————————————————————————————————————————————————
#
# 「这一轮卡住了」有别处在管（输入框上方的计时、`cheese status` 的判停），这里管的是另一件：
# 这件事好几天没人动了。一个判据，三处用：三天提醒负责人一次，十四天从侧栏收起、
# 在任务列表里收进「已停滞」、在项目总览的进展里记一笔。

#: 多久没有动静，提醒负责人一次。
QUIET_NOTICE_AFTER = timedelta(days=3)
#: 多久没有动静算「已停滞」。
STALLED_AFTER = timedelta(days=14)

#: 在等别人审阅、等人回答：球在别人手上，这件事不是没人管。
_WAITING_ON_SOMEONE = frozenset(
    {NeedsYou.awaiting_review.value, NeedsYou.awaiting_answer.value}
)


def quiet_since(shown: Presentation, *, last_activity: datetime) -> datetime | None:
    """这件事从什么时候起没有动静。None = 不算：已经结束、在等别人。

    `last_activity` 是最后一次有人或芝士在任务里说话的时刻。队友说它在做不算动静：
    说过一句「在做」、之后三天什么都没说的活，正是要提醒负责人的那种。
    """
    if shown.column in (Column.done, Column.archived):
        return None
    if shown.phrase in _WAITING_ON_SOMEONE:
        return None
    return last_activity


def is_stalled(shown: Presentation, *, last_activity: datetime, now: datetime) -> bool:
    """已停滞：`STALLED_AFTER` 这么久没有动静。"""
    since = quiet_since(shown, last_activity=last_activity)
    return since is not None and now - since >= STALLED_AFTER


# —— 一个房间 ——————————————————————————————————————————————————


def room_presentation(facts: RoomFacts, *, now: datetime) -> Presentation:
    """一个房间此刻在哪一列、显示哪句话。

    和一条活共用「关了就是结束」「待回答压过在做」两条。不同的是：房间会归档
    （活不会）；房间的「运行中」看本轮在不在跑（`RoomFacts.running`），而且压过卡；
    活的队友会话在跑排在卡后面（卡说的是下一步在谁手上），而且要是它没报过的那一次跑。

    `now` 收在签名里是为了和 `task_presentation` 同一个形状 —— 房间今天没有需要
    对时间的判据，但调用方不该为这个差别写两种调用。
    """
    del now  # 见 docstring：形状对齐，房间暂时没有按时间判的格子。

    if facts.accepted_at is not None:
        return _show(Done.accepted)
    if facts.status == TopicStatus.archived:
        return _show(Archived.archived)

    # 见 `task_presentation` 里同一格的理由：提问压过「运行中」，因为本轮不会自己
    # 往下走了。
    if facts.awaiting_answer:
        return _show(NeedsYou.awaiting_answer)

    if facts.running:
        return _show(Building.running)

    if facts.card is not None:
        shown = _card_presentation(facts.card)
        if shown is not None:
            return shown

    # 草稿是「还没开工」，正是 building 的定义（还没递出交付）。它和空闲要分开：
    # 一个从没开始的房间和一个做完一轮在等下一句话的房间，不是一回事。
    if facts.status == TopicStatus.draft:
        return _show(Building.draft)
    return _show(Building.idle)
