"""看板上一条活/一个房间落在哪一列，以及卡面上显示哪一句话。

一句话：**显示状态从不存库，读的时候从已有事实算**，而且在后端算一次。

这条逻辑本来在前端，而且是两份 —— `lib/topicState.ts` 算房间，`lib/taskRing.ts`
算一条活，两份各写了一遍「在跑压过验收卡」这条规矩，其中一份的注释还指着另一份。
同一条规矩写两遍，就是它们迟早走散的样子；而 CLI、通知、以后任何一个客户端问的
是同一个问题，却谁也读不到那两份 TypeScript。所以答案搬到这里，前端只负责画。

## 列回答的不是「进行到哪一步」，是「**该谁动**」

这是整件事的核心，不是给现有状态换个分组：

- `building` 施工中 —— 还没递出交付。
- `delivering` 交付中 —— 下一步在**平台/芝士**手上。
- `needs_you` 等你 —— 下一步在**人**手上。
- `done` 已完成 —— 已采纳，或已收工且没交付。
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
`facts_for_room` 是仅有的两处读行对象的地方，它们也只读属性、不查库。
"""

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.domain.review.notes import NoteCode, NoteLevel, note_level
from app.domain.room_task.models import Task, TaskStatus
from app.domain.topic.models import Topic, TopicStatus

if TYPE_CHECKING:
    from app.domain.review.models import AcceptCard

#: 一行说自己 `running`、却已经这么久没有任何动静 —— 那就不能说它在跑。
#:
#: 这个数是量出来的，不是拍的。信号取的是**这条活最后一个 block 的时间**（见
#: `TaskRepository.last_block_at_for_tasks`）：一轮里每一步都落 block，在真实的一
#: 条活上实测，轮内间隔中位数 8 秒、p90 34 秒。10 分钟是 p90 的十几倍，一段安静的
#: 工具活动撑不到它；而一条真的停住的活，10 分钟就在看板上现形，不用等两小时。
#:
#: 为什么不能拿 `Task.last_turn_at` 当信号：它只在**认领分身**那一刻盖一次，之后
#: 不再刷新，所以按它算，宽限期必须长过最长的一条活。它只作兜底 —— 一条刚被认领、
#: 还没来得及说第一句话的活，靠的是它。
LOST_SIGNAL_AFTER = timedelta(minutes=10)


class Column(enum.StrEnum):
    """看板的列。判据是「该谁动」，见模块开头。"""

    building = "building"
    delivering = "delivering"
    needs_you = "needs_you"
    done = "done"
    archived = "archived"


class Building(enum.StrEnum):
    """还没递出交付。"""

    running = "运行中"
    idle = "空闲"
    #: 房间才有：还没开工。活没有草稿态。
    draft = "草稿"
    #: 说在跑，但没有任何东西最近确认过。和「空闲」分开，是因为一条隧道断掉的活
    #: 和一条真的没人找它的活，对看的人意味着完全相反的下一步。
    lost = "失联"


class Delivering(enum.StrEnum):
    """下一步在平台/芝士手上，人不用动。"""

    gate_running = "检查运行中"
    awaiting_checks = "等待检查"
    fixing_checks = "修复检查"
    #: 采纳时撞了合并冲突，芝士已经被派去解 —— 今天 `NoteCode.merge_conflict`
    #: 就在写（review/services.py 两处），所以这一格是点得亮的，不是空契约。
    resolving_conflict = "解决冲突"
    awaiting_merge = "等待合并"


class NeedsYou(enum.StrEnum):
    """下一步在人手上。"""

    checks_failed = "检查未通过"
    awaiting_review = "等待验收"
    bounced = "交付被退回"


class Done(enum.StrEnum):
    accepted = "已采纳"
    closed = "已收工"


class Archived(enum.StrEnum):
    archived = "已归档"


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
    """可以直接画出来的一格：哪一列，卡面写什么。"""

    column: Column
    display_status: str

    def as_dict(self) -> dict[str, str]:
        return {"column": str(self.column), "display_status": self.display_status}


def _show(phrase: Phrase) -> Presentation:
    """列不是挑出来的，是从短语查出来的 —— 见模块开头。"""
    return Presentation(column=_COLUMN_OF[type(phrase)], display_status=phrase.value)


# —— 事实 ——————————————————————————————————————————————————————
#
# 显式的小结构，而不是直接吃 ORM 行：这样这层测得起来（构造一行 `Task` 要先有
# 一个 session 和一堆和本题无关的必填列），也说清了它到底读了哪几个字段。


@dataclass(frozen=True, slots=True)
class CardFacts:
    """这条活/这个房间最新那张验收卡，窄到只剩看板要读的部分。"""

    status: str
    #: 卡停在什么上。文案在 `note` 里，判断只看码 —— `review/notes.py` 的规矩。
    note_code: NoteCode | None = None
    pr_merged_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TaskFacts:
    status: str
    #: 最后一次有东西确认这条活还在动。见 `LOST_SIGNAL_AFTER`：优先是它最后一个
    #: block 的时间，没说过话就退回它是什么时候被认领的。
    last_signal_at: datetime | None
    accepted_at: datetime | None
    card: CardFacts | None
    #: 有没有分身在做这条活（`Task.subagent_id`）。
    has_worker: bool = False
    #: 那个分身活在**房间的**会话里，所以房间的屏幕没了，它一定也没了 —— 这一位
    #: 是跑轮次的进程当下的事实（`ChatService.has_live_screen`），不是一列时间戳，
    #: 所以它得从外面喂进来（这一层不碰 I/O）。
    room_screen_live: bool = True
    #: 结论已经回流、正等房间结算。分身是干完了在等人，不是断了。
    conclusion_pending: bool = False


@dataclass(frozen=True, slots=True)
class RoomFacts:
    status: str
    #: 本轮是否在跑。房间的这一位来自跑轮次的进程本身（内存里的当下事实），不是
    #: 一列陈旧的时间戳 —— 所以房间没有「失联」这一格：它没有可能过期的东西。
    running: bool
    accepted_at: datetime | None
    card: CardFacts | None


def facts_for_card(card: "AcceptCard | None") -> CardFacts | None:
    if card is None:
        return None
    return CardFacts(
        status=str(card.status),
        note_code=card.note_code,
        pr_merged_at=card.pr_merged_at,
    )


def facts_for_task(
    task: Task,
    card: "AcceptCard | None" = None,
    last_block_at: datetime | None = None,
    *,
    room_screen_live: bool = True,
    conclusion_pending: bool = False,
) -> TaskFacts:
    """把一行 `Task`（加上它的卡、加上它最后一次说话的时间）折成这层要读的事实。

    两个信号取晚的那个，因为它们各自会缺：一条刚被认领、还没说第一句话的活只有
    `last_turn_at`；一条干了很久的活，`last_turn_at` 停在认领那一刻，真正在动的
    证据在 block 上。取晚的 = 「有任何一个东西确认过它还活着」。
    """
    signals = [t for t in (last_block_at, task.last_turn_at) if t is not None]
    return TaskFacts(
        status=str(task.status),
        last_signal_at=max(signals) if signals else None,
        accepted_at=task.accepted_at,
        card=facts_for_card(card),
        has_worker=bool(task.subagent_id),
        room_screen_live=room_screen_live,
        conclusion_pending=conclusion_pending,
    )


def facts_for_room(
    topic: Topic, running_ids: set[uuid.UUID], card: "AcceptCard | None" = None
) -> RoomFacts:
    return RoomFacts(
        status=str(topic.status),
        running=topic.id in running_ids,
        accepted_at=topic.accepted_at,
        card=facts_for_card(card),
    )


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

    先读码再读 status，因为码说的是「卡停在什么上」，比 status 具体：一张 `pr_open`
    的卡带着 `repush_failed`，说的是「PR 上那个红是旧的、芝士推不上新的去清它」，
    不是「还在等 CI」。
    """
    if card.status in _SETTLED_CARD:
        return None

    # 平台已经把这个红交回给芝士去修 —— 下一步在芝士手上，别去催人。
    if card.note_code is NoteCode.checks_failed:
        return _show(Delivering.fixing_checks)
    # 采纳时撞了冲突，芝士被派去解；解完由人重试采纳，但此刻在推的是平台。
    if card.note_code is NoteCode.merge_conflict:
        return _show(Delivering.resolving_conflict)
    # 芝士的修复推不上 GitHub / 本地分支和 PR 分支分叉了：PR 上的红清不掉，而且
    # 没有任何自动的路能清掉它。这就是「CI 红了没人管」。
    if card.note_code in (NoteCode.repush_failed, NoteCode.repush_diverged):
        return _show(NeedsYou.checks_failed)
    # 其余所有「停住了」的码。刻意不再列一遍名字：`notes.py` 已经维护着那张表，
    # 而它的注释说得很清楚 —— 漏进 info 的码会和「还在等检查」长得一模一样。新增
    # 的停住码在这里自动落到「等你」，而不是安静地被算成还在走。
    if _is_stuck(card.note_code):
        return _show(NeedsYou.bounced)

    if card.status == "pending_gate":
        return _show(Delivering.gate_running)
    if card.status == "pending":
        return _show(NeedsYou.awaiting_review)
    if card.status == "conflict":
        return _show(NeedsYou.bounced)
    if card.status == "pr_open":
        # 合过了，等落地；否则 PR 开着等检查。
        if card.pr_merged_at is not None:
            return _show(Delivering.awaiting_merge)
        return _show(Delivering.awaiting_checks)
    # 认不出来的状态不冒充答案 —— 让调用方的其余判据接着说。
    return None


# —— 一条活 ————————————————————————————————————————————————————


def task_presentation(facts: TaskFacts, *, now: datetime) -> Presentation:
    """一条活此刻在哪一列、显示哪句话。

    两条优先级规矩，都是从今天前端那两份里原样搬来的，不是新发明的：

    1. **已交付压过一切**。交付和 open/closed 不是同一个问题：一条活可以已交付却
       还开着（有人继续往同一条分支推），也可以关掉却什么都没交付。
    2. **活的事实压过纸面**。`running` 压过验收卡说的一切 —— 卡描述的是它可能马上
       就要顶掉的那一版，而「在跑」是此刻真的成立的那件事。
    """
    if facts.accepted_at is not None:
        return _show(Done.accepted)

    # 有分身在做这条活。它住在**房间的**会话里，所以「它还在不在」有两个答案，
    # 先问屏幕：房间的屏幕没了，它一定也没了 —— 而它自己不会来说一声。
    #
    # 结论已经回流的不算在内：那是干完了在等房间结算，不是还在做。
    worker_on_it = (
        facts.has_worker
        and facts.status == TaskStatus.open
        and not facts.conclusion_pending
    )
    alive = facts.room_screen_live and not _lost_signal(facts.last_signal_at, now=now)
    # 规矩 2：在跑压过纸面。
    if worker_on_it and alive:
        return _show(Building.running)

    if facts.card is not None:
        shown = _card_presentation(facts.card)
        if shown is not None:
            return shown

    # 说自己有人在做，却没有任何东西确认过 —— **在卡说完之后才轮到这一句**。一条
    # 递了卡、安静地等人验收的活，安静得理直气壮：它不是断了联系，它在等你。分身
    # 干完活并不会把 `subagent_id` 抹掉，所以抢在卡前面说，等于把每一条等验收的活
    # 都误报成失联。
    if worker_on_it:
        return _show(Building.lost)

    # 放在最后：一条已交付的活即使关掉了，它首先是已交付的（规矩 1 已经拦了它）。
    if facts.status == TaskStatus.closed:
        return _show(Done.closed)
    return _show(Building.idle)


def _lost_signal(last_signal_at: datetime | None, *, now: datetime) -> bool:
    """这一行说自己在跑，但还有东西确认这件事吗？

    一个信号都没有过也算失联：那意味着既没说过话、也没有一次开跑被记下来，而
    「没有证据」不能读成「一切正常」。
    """
    if last_signal_at is None:
        return True
    return now - last_signal_at > LOST_SIGNAL_AFTER


# —— 一个房间 ——————————————————————————————————————————————————


def room_presentation(facts: RoomFacts, *, now: datetime) -> Presentation:
    """一个房间此刻在哪一列、显示哪句话。

    和一条活同两条优先级规矩。差别只有两处，都是因为房间和活确实不是一种东西：
    房间会归档（活不会），房间没有「失联」（见 `RoomFacts.running`）。

    `now` 收在签名里是为了和 `task_presentation` 同一个形状 —— 房间今天没有需要
    对时间的判据，但调用方不该为这个差别写两种调用。
    """
    del now  # 见 docstring：形状对齐，房间暂时没有按时间判的格子。

    if facts.accepted_at is not None:
        return _show(Done.accepted)
    if facts.status == TopicStatus.archived:
        return _show(Archived.archived)

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
