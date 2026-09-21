"""待我处理：清单上的一行长什么样。

## 为什么不能读通知表

通知是**一条条事件记录**，不是状态。验收卡递上来发一条通知，之后它被驳回、被作废、
或者别人先处理掉了，那条记录还躺在表里，而从它身上读不出它已经不作数了。一个「待我
处理」的列表要回答的恰恰是「现在还没处理完的有哪些」，所以它必须从当下的事实重新算
一遍，而不是把收件箱换个标题。

## 判据不在这里，在 `delivery/addressing.py`

「点到的是谁」曾经在这里有一份，而投递那一侧另有一份 —— 同一个问题两处各答一遍，
就是看板显示「待处理」而没有人被通知到的那个形状。现在只有一份：清单和投递都调
`address()`，通知负责把人叫回来，清单负责他回来之后不用自己翻。

看板列出一个项目里全部待处理项；这份清单列出跨项目、且点到我的那些。两者读的是同一
个 `presentation`：一件事在看板上是待处理，在那份清单里就是待处理，不可能一处说要人
动手、另一处说不用。差别只有范围 —— 从一个项目扩到我能看见的全部项目。

## 这里只有形状，没有 I/O

拉哪些项目、哪些房间、哪些卡，是调用方的事（`api/routes/awaiting.py`）。一个横跨五
个领域的聚合如果把查询也收进来，就得从 room_task 里直接摸另外四个领域的 repository
—— `test_domain_import_guard` 拦的正是这个，而它拦得对：那样这一层就再也不能单独读
懂了。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


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
    #: 为什么在等他 —— `delivery/addressing.py` 的那几个码。
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
