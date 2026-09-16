"""待我处理：一件待处理的事项**点的是谁**。

## 为什么不能读通知表

通知是**一条条事件记录**，不是状态。验收卡递上来发一条通知，之后它被驳回、被作废、
或者别人先处理掉了，那条记录还躺在表里，而从它身上读不出它已经不作数了。一个「待我
处理」的列表要回答的恰恰是「现在还没处理完的有哪些」，所以它必须从当下的事实重新算
一遍，而不是把收件箱换个标题。

## 规则同一份，收件人筛选加在规则之后

看板列出一个项目里全部待处理项；「待我处理」列出跨项目、且点到我的那些。两者读的是
同一个 `presentation`：一件事在看板上是待处理，在那个清单里就是待处理，不可能一处说
要人动手、另一处说不用。差别只有两点 —— 范围从一个项目扩到我能看见的全部项目，以及
在算完之后按这里的判据过滤。

## 谁算被点到

- **验收卡上的验收人**：卡是递给他的。
- **提需求的人**（`Task.reporter_handle`）：他等的东西有了结果。
- **发起那一轮的人**：芝士停在一个待确认问题上，只有他能回答。

这三条就是 #1084 定的收件人，和通知投给谁是同一份判据 —— 通知负责把人叫回来，清单
负责他回来之后不用自己翻。

## 和 `presentation.py` 同一个分工

这里只有判据，没有 I/O：拉哪些项目、哪些房间、哪些卡，是调用方的事
（`api/routes/awaiting.py`）。一个横跨五个领域的聚合如果把查询也收进来，就得从
room_task 里直接摸另外四个领域的 repository —— `test_domain_import_guard` 拦的正是
这个，而它拦得对：那样这一层就再也不能单独读懂了。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.domain.review.models import AcceptCard
from app.domain.room_task.models import Task

#: 为什么在等我。前端按它给每一行配一句话。
REASON_REVIEWER = "reviewer"
REASON_REPORTER = "reporter"
REASON_ASKED = "asked"


@dataclass(frozen=True, slots=True)
class WaitingItem:
    """一件在等这个人的事，已经可以直接画出来。"""

    project_id: uuid.UUID
    project_name: str
    topic_id: uuid.UUID
    topic_title: str
    #: 这是房间自己的事（None），还是房间里某一条活的事。
    task_id: uuid.UUID | None
    task_title: str | None
    #: 看板算的那一格 —— 和项目看板上同一个函数、同一句话。
    display_status: str
    reason: str
    #: 排序用：这件事最后一次动是什么时候。
    at: datetime

    def as_dict(self) -> dict:
        return {
            "projectId": str(self.project_id),
            "projectName": self.project_name,
            "topicId": str(self.topic_id),
            "topicTitle": self.topic_title,
            "taskId": None if self.task_id is None else str(self.task_id),
            "taskTitle": self.task_title,
            "displayStatus": self.display_status,
            "reason": self.reason,
            "at": self.at.isoformat(),
        }


def why_a_task_is_mine(
    task: Task, card: AcceptCard | None, handle: str, *, asked: bool
) -> str | None:
    """这条活为什么在等 `handle` —— None = 不在等他。

    `asked` 要求调用方已经把「这个问题在等谁」算进去：它为真意味着这一轮是这个人
    发起的，而不只是这条活停在一个提问上。凭房间名册推收件人，等于把一条只有一个
    人该处理的事项摆进一屋子人的清单里。
    """
    if asked:
        return REASON_ASKED
    if card is not None and card.reviewer_handle == handle:
        return REASON_REVIEWER
    if task.reporter_handle == handle:
        return REASON_REPORTER
    return None


def why_a_room_is_mine(
    card: AcceptCard | None, handle: str, *, asked: bool
) -> str | None:
    """同一个判据，问的是房间自己那条线。

    房间没有「提需求的人」这一栏 —— 那是一条活上的字段，所以这里只有两条。
    """
    if asked:
        return REASON_ASKED
    if card is not None and card.reviewer_handle == handle:
        return REASON_REVIEWER
    return None
