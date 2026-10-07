"""看板上一条活/一个房间落在哪一列，以及卡面上显示哪一句话。

一句话：**显示状态从不存库，读的时候从已有事实算**，而且在后端算一次。

这条逻辑本来在前端，而且是两份 —— `lib/topicState.ts` 算房间，`lib/taskRing.ts`
算一条活，两份各写了一遍「在跑压过验收卡」这条规矩，其中一份的注释还指着另一份。
同一条规矩写两遍，就是它们迟早走散的样子；而 CLI、通知、以后任何一个客户端问的
是同一个问题，却谁也读不到那两份 TypeScript。所以答案搬到这里，前端只负责画。

## 列回答的不是「进行到哪一步」，是「**该谁动**」

这是整件事的核心，不是给现有状态换个分组：

- `not_started` 未开始 —— 任务还在讨论，负责人还没点「开始」。
- `building` 进行中 —— 开始了，还没递出交付。
- `delivering` 检查中 —— 下一步在**平台/芝士**手上。
- `needs_you` 待处理 —— 下一步在**人**手上。
- `done` 已完成 —— 已采纳，或已关闭且没交付。
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
from typing import TYPE_CHECKING, Protocol

from app.core.errors import ValidationError
from app.domain.review.notes import NoteCode, NoteLevel, note_level
from app.domain.room_task.binding import catalog_id, resolve

# `Task` 是这一层唯一还拿在手里的 ORM 行 —— **暂留**，不是读模型。方案 v6 的第一期
# 只给 block / review 开了窄读出口；项目 rail 的 Task 暂交给 task_liveness、
# facts_for_task 和 TaskOut.model_validate。本模块的 card_model 也读取 Task 的绑定
# 属性，计算卡面的模型名；facts_for_task 和 card_model 都只读属性、不顺着行查库。
# 等 room_task 有自己的窄读输出，再替换这份 ORM 边界。
from app.domain.room_task.models import Task, TaskStatus
from app.domain.topic.models import Topic, TopicStatus

if TYPE_CHECKING:
    # 本领域内的消费者手上是 `AcceptCard` 那一行，它是这一层唯一需要按名字说出来的
    # 形状 —— 只在 `CardSignals` 的并集里出现，所以放在 TYPE_CHECKING 下：这一层是
    # 纯的，导入它只为签名，运行时一行都不碰。
    from app.domain.review.models import AcceptCard


class CardSignals(Protocol):
    """一张卡能被折成 `CardFacts` 的五个信号 —— 折卡的人只读这五个，不多不少。

    这是一份**结构**契约，不是某一种形状：`AcceptCard` 那一行满足它，别的领域从窄
    读出口交出来的纯值也满足它，两边都不必把自己交出来给这一层当类型。刻意这么写
    而不是把对方那个类标进签名 —— 标注一个类型就是 `room_task` 指向那个领域的一条
    边，而这一层与 `review` 之间已经有两条类型边（`review.models`、
    `review.notes`，都冻结在 `.importlinter` 里）。C3 要的是无环：再加一条，环就回
    来了，而「加一条豁免让它绿」不是解法，是把那条规矩让掉。契约式写法下，对方换
    成什么形状都行，只要这五个读得出来。

    声明成 property 而不是普通属性是有意的：pyright 对只读属性按**协变**比对，所以
    `AcceptStatus`（`str` 的子类）能满足 `status: str`，`RailCard` 的 `dict` 也能满足
    `Mapping[str, object]`；写成可变属性就必须一模一样，两边都过不去。

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


class Column(enum.StrEnum):
    """看板的列。判据是「该谁动」，见模块开头。"""

    not_started = "not_started"
    building = "building"
    delivering = "delivering"
    needs_you = "needs_you"
    done = "done"
    archived = "archived"


class NotStarted(enum.StrEnum):
    """任务才有：还在讨论，负责人还没点「开始」。"""

    discussing = "discussing"


class Building(enum.StrEnum):
    """开始了，还没递出交付。"""

    running = "running"
    #: 任务才有：开始了，此刻没有在跑的一轮，也还没递出交付。
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
    #: 芝士提出了待确认问题，本轮停止等待回答。这是唯一一种**会中断运行**的：
    #: 其余几格都是一轮结束之后的状态。
    awaiting_answer = "awaiting_answer"


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
Phrase = NotStarted | Building | Delivering | NeedsYou | Done | Archived

#: 短语 → 它属于哪一列。**唯一**一处把两者关联起来的地方。
_COLUMN_OF: dict[type[enum.StrEnum], Column] = {
    NotStarted: Column.not_started,
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
    #: 负责人点过「开始」（`Task.started_at`）。
    started: bool = False
    #: 任务自己的会话此刻有一轮在跑 —— 跑轮次的进程当下的事实，从外面喂进来。
    running: bool = False
    #: 关闭时留下过结论（`Task.conclusion`）。
    has_conclusion: bool = False
    #: 最近一条提问消息还没有回答（`BlockRepository.awaiting_an_answer`）。
    #: 回答记在提问那一块上，所以这一位不需要新增存储；但它要查一次库，所以和别的
    #: 事实一样从外面喂进来。
    awaiting_answer: bool = False


@dataclass(frozen=True, slots=True)
class RoomFacts:
    status: str
    #: 本轮是否在跑。房间的这一位来自跑轮次的进程本身（内存里的当下事实），不是
    #: 一列陈旧的时间戳 —— 所以房间没有「失联」这一格：它没有可能过期的东西。
    running: bool
    accepted_at: datetime | None
    card: CardFacts | None
    #: 见 `TaskFacts.awaiting_answer`，问的是房间自己那条线。
    awaiting_answer: bool = False


def facts_for_card(card: "AcceptCard | CardSignals | None") -> CardFacts | None:
    """这张卡要读的几位，折成纯值。`None` 是「这条活上没有卡」，不是一张空卡。

    收两种形状是因为卡有两处来源：领域内的人手上是 `AcceptCard` 那一行，HTTP 路由
    手上是 `review` 窄读出口交出来的纯值 —— 一份冻结的值，没有 session 可以顺着多
    查一行。这一层只读 `status` / `note_code` / `merge_state` / `decided_by` /
    `auto_merge_armed_by` 五个属性（`CardSignals`），两种形状都长得出来，所以折出来
    的 `CardFacts` 一模一样（`tests/unit/test_presentation.py` 钉住了这件事）。
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
    task: Task,
    card: "AcceptCard | CardSignals | None" = None,
    *,
    running: bool = False,
    awaiting_answer: bool = False,
) -> TaskFacts:
    """把一行 `Task`（加上它的卡）折成这层要读的事实。"""
    return TaskFacts(
        status=str(task.status),
        accepted_at=task.accepted_at,
        card=facts_for_card(card),
        started=task.started_at is not None,
        running=running,
        has_conclusion=bool(task.conclusion),
        awaiting_answer=awaiting_answer,
    )


def facts_for_room(
    topic: Topic,
    running_ids: set[uuid.UUID],
    card: "AcceptCard | None" = None,
    *,
    awaiting_answer: bool = False,
) -> RoomFacts:
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


def card_waits_on_reviewer(card: "AcceptCard") -> bool:
    """这张卡此刻是不是真的在等验收人动手 —— 看板「待审阅」那一格。

    只是 `pending` 不够：CI 挂了、合并冲突、平台在换基、合并态还没看过，这些时候
    采纳按钮点不了，下一步在芝士或平台手上。侧栏的黄灯和「与我的相关性」都问它，
    这样两边不会一边说「等你」、一边说「芝士在修」。
    """
    facts = facts_for_card(card)
    return facts is not None and _card_presentation(facts) == _show(
        NeedsYou.awaiting_review
    )


def card_needs_agent_fix(card: "AcceptCard") -> bool:
    """这张卡是不是停在「检查红了 / 冲突了 / 被退回，要 AI 去修」上。

    侧栏红灯的「检查报错没人处理」问它：卡还停在这里，这件事就还没了；检查重跑
    绿了、冲突解了、卡被撤了，它自然不再成立 —— 不靠「AI 说没说过话」去猜。
    """
    return agent_fix_kind(card) is not None


def agent_fix_kind(card: "AcceptCard") -> str | None:
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

    三条优先级规矩：

    1. **已交付压过一切**。交付和 open/closed 不是同一个问题：一条活可以已交付却
       还开着（有人继续往同一条分支推），也可以关掉却什么都没交付。
    2. **待回答压过「在跑」**：进程可能还在，但它不会自己往下走了，而看板显示
       「运行中」正是让人不来看的那一句。
    3. **活的事实压过纸面**。`running` 压过验收卡说的一切 —— 卡描述的是它可能马上
       就要顶掉的那一版，而「在跑」是此刻真的成立的那件事。

    `now` 收在签名里是为了和 `room_presentation` 同一个形状。
    """
    del now
    if facts.accepted_at is not None:
        return _show(Done.accepted)
    if facts.status == TaskStatus.closed:
        return _show(Done.completed if facts.has_conclusion else Done.closed)
    if facts.awaiting_answer:
        return _show(NeedsYou.awaiting_answer)
    if not facts.started:
        return _show(NotStarted.discussing)
    if facts.running:
        return _show(Building.running)
    if facts.card is not None:
        shown = _card_presentation(facts.card)
        if shown is not None:
            return shown
        # 退回、闸门判红：卡结算了，球交回给芝士去改。此刻没有一轮在改（`running`
        # 在上面已经答过），也没有新卡递上来，这件事就停在了负责人手上 —— 侧栏把它
        # 标成没人处理，这里不能还说「已开始」。
        if facts.card.status in _BOUNCED_TO_AGENT:
            return _show(NeedsYou.bounced)
    return _show(Building.started)


#: 负责人自己要动手的那几格（「需要我处理」的负责人那一条）：芝士把任务文档写好了
#: 等他点「开始」，检查红了芝士清不掉，被退回之后没人接着改。只是任务开着、在等别人
#: 审阅或回答，都不算 —— 那是别人手上的事。
_OWNER_ACTS_ON = frozenset(
    {
        NotStarted.discussing.value,
        NeedsYou.checks_failed.value,
        NeedsYou.bounced.value,
    }
)


def owner_acts_on(shown: Presentation, *, running: bool) -> bool:
    """这一格的下一步是不是在负责人手上。

    「讨论中」还在跑的那一段是芝士在写任务文档初稿，写完之前没有东西可以开始。
    """
    if running:
        return False
    return shown.phrase in _OWNER_ACTS_ON


# —— 停滞 ——————————————————————————————————————————————————————
#
# 「这一轮卡住了」有好几处在管（侧栏的红、输入框上方的计时），这里管的是另一件：
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


def quiet_since(
    shown: Presentation, *, running: bool, last_activity: datetime
) -> datetime | None:
    """这件事从什么时候起没有动静。None = 不算：有一轮在跑、已经结束、在等别人。

    `last_activity` 是最后一次有人或芝士在任务里说话的时刻。
    """
    if running or shown.column in (Column.done, Column.archived):
        return None
    if shown.phrase in _WAITING_ON_SOMEONE:
        return None
    return last_activity


def is_stalled(
    shown: Presentation, *, running: bool, last_activity: datetime, now: datetime
) -> bool:
    """已停滞：`STALLED_AFTER` 这么久没有动静。"""
    since = quiet_since(shown, running=running, last_activity=last_activity)
    return since is not None and now - since >= STALLED_AFTER


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
