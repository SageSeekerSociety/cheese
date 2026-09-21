"""一条事件点到了谁 —— 全仓唯一的那一份判据（结论 13、14、15，不变量 I11）。

## 为什么有这个模块

「下一步在谁手上」以前有三个互不引用的答案：

- `room_task/presentation.py` 的列（`needs_you` 就是「下一步在人手上」）；
- `room_task/awaiting.py` 的两条判据（验收人 / 提需求的人 / 被问的那个人）；
- `agent/announce.py` 的那道闸门：`meta.who` 不是 `human` 而调用点又点了收件人，
  就抛 `ValueError`，同时还兼着「agent 不能当收件人」。

三个答案各自都对得起自己那一处，合起来就是看板显示「待处理」而没有人被通知到。
这个模块把它们收成一个入口：**一条事件说它点了谁的名，`address()` 说谁会收到、
凭什么收到。**

「下一步在谁手上」本来就在两处被声明过，所以这里不新造第三处，只把那两处翻成
`Hand`：看板那一列（`hand_of`）和通知契约的 `who` 码（`agent/announce.py` 的
`_HAND_OF_WHO`，`platform` / `cheese` → 平台手上，`human` → 参与者手上）。两张都是
封闭表，多一档而没跟上就当场抛 `KeyError`。

## 唯一的收件人规则（结论 15）

一件事从「下一步在平台手上」转入「下一步在某个参与者手上」的那一刻，通知那个参与
者，一次。不再「一律告诉人」：Cloud 正在开机是平台自己的事，不惊动任何人；一轮失
败而下一步回到了点名它的那个参与者，就只通知那一个。

`Hand` 因此只有两档。「已完成」「已归档」这些没有下一步的情形也落 `platform`：它们
和「平台正在处理」对投递的意思一模一样（谁都不通知），给它们第三个值只是多一个行为
完全相同的档。

## 收件人只能从事件**点的名**来，不能从名册推

凭房间名册推一批收件人出来，等于把一条只有一个人该处理的事项摆进一屋子人的清单
里。所以 `Event` 上的三种关系是封闭的 —— 一条事件点到一个参与者，只可能是这三种
之一，每一种都是那个参与者和这件事之间一个具体的事实：

| 关系 | 凭什么是他 |
|---|---|
| 验收人 | 卡是递给他的 |
| 提需求的人 | 他等的东西有了结果 |
| 被问的那个人 | 芝士停在一个待确认问题上，只有他能回答 |

这三条就是 #1084 定的收件人。「待我处理」那份清单和投递读的是同一份判据 —— 通知负
责把人叫回来，清单负责他回来之后不用自己翻。

## 纯函数

没有 I/O，不碰 session，不读时钟，也不问「现在是谁在看」：`address()` 的答案只取决
于这条事件本身，所以同一条事件算两遍得到同一个答案 —— 「恰好一次」因此才是可执行
的（重算不会变成第二次打扰）。

怎么送到收件人手上是另一个问题：人有浏览器、agent 有一条会话，那一叉在
`identity/arrival.py`，是全系统唯一合法的人 / agent 分叉。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from app.domain.room_task.presentation import Column

#: 为什么是他。前端按它给「待我处理」的每一行配一句话。
REASON_REVIEWER = "reviewer"
REASON_REPORTER = "reporter"
REASON_ASKED = "asked"


class Hand(enum.StrEnum):
    """下一步在谁手上。两档，封闭。"""

    #: 平台或芝士自己在推进，或者这件事已经没有下一步 —— 谁都不该被惊动。
    platform = "platform"
    #: 下一步在某个参与者手上。
    participant = "participant"


#: 看板的列 → 下一步在谁手上。**封闭表**：`Column` 多一档而这里没跟上，查表当场
#: 抛 `KeyError`，而不是让新的一列悄悄落进「不通知任何人」那一档。
_HAND_OF_COLUMN: dict[Column, Hand] = {
    Column.building: Hand.platform,
    Column.delivering: Hand.platform,
    Column.needs_you: Hand.participant,
    Column.done: Hand.platform,
    Column.archived: Hand.platform,
}


def hand_of(column: Column) -> Hand:
    """看板上这一格的下一步在谁手上。

    看板的列回答的本来就是「该谁动」（见 `presentation.py` 模块开头），所以这里不
    是第二次判断，是把同一个答案翻译成投递这一侧的词。
    """
    return _HAND_OF_COLUMN[column]


@dataclass(frozen=True, slots=True)
class Event:
    """一条事件，窄到只剩寻址要读的部分：它点了谁的名。

    `reviewers` 是复数而另外两个不是，因为只有验收这一种关系可能同时点到几个人：
    一张卡有一个验收人，而一条作废了某张批准票的事件还点到投那一票的人 —— 重新投
    一次这件事只有他能做。提需求的人和被问的那个人各只有一个。
    """

    reviewers: tuple[str, ...] = ()
    reporter: str | None = None
    asked: str | None = None


@dataclass(frozen=True, slots=True)
class Recipient:
    """一个收件人，连同凭什么是他。"""

    handle: str
    reason: str


@dataclass(frozen=True, slots=True)
class Addressed:
    """寻址结果 —— 投递的入参是它，不是一句文案（I11）。

    原因跟着人走，不是整条事件一个：递卡同时点到验收人和提需求的人，两个人收到同
    一句话的理由并不相同，而「待我处理」要显示的正是**我**这一行的那个理由。
    """

    recipients: tuple[Recipient, ...] = ()

    def reason_for(self, handle: str) -> str | None:
        """这条事件凭什么点到 `handle` —— None = 没点到他。"""
        for r in self.recipients:
            if r.handle == handle:
                return r.reason
        return None


#: 谁都不通知。
NOBODY = Addressed()

#: 这条事件谁的名也没点。投递入口用它当默认值 —— 不说点了谁，就不惊动任何人。
NAMES_NOBODY = Event()


def address(event: Event, next_hand: Hand) -> Addressed:
    """这条事件点到了谁。

    下一步在平台手上就没有收件人 —— 那一档是结论 15 收紧「一律告诉人」的那一半。

    `next_hand` 不是这里算的，也不由点名的那个调用点填：它从已经声明过这件事的地方
    翻过来 —— 「待我处理」那份清单从看板那一列（`hand_of`），投递从通知契约的 `who`
    码（`agent/announce.py`）。所以「平台在处理，另外通知这几个人」这句话**写得出
    来、发不出去**：调用点照样可以一边报平台在处理一边点几个人的名，那些名字在这里
    被忽略，一个人也不通知。事件点谁的名是一件事，下一步在谁手上是另一件，后者不归
    点名的人说。

    同一个人只出现一次，带他最强的那个理由 —— 下面这个顺序就是判据本身：一个待确
    认问题挡住其余所有事，所以「被问的那个人」压过「验收人」，而卡递给谁又比「他
    等的东西有结果了」具体。空白和重复在这里消掉，调用点不必各自去重（以前那一步
    是一路拖到「handle → 用户 id」时被 set 顺手吃掉的）。
    """
    if next_hand is Hand.platform:
        return NOBODY

    pointed: list[Recipient] = []
    seen: set[str] = set()
    for handle, reason in (
        (event.asked, REASON_ASKED),
        *((h, REASON_REVIEWER) for h in event.reviewers),
        (event.reporter, REASON_REPORTER),
    ):
        name = (handle or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        pointed.append(Recipient(handle=name, reason=reason))
    return Addressed(tuple(pointed))
