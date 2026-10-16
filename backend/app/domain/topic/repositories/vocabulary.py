"""这一包对外说出去的两组词和两件值 —— 从 `repositories.py` 拆出来的。

`TopicSortField` / `SortOrder` 是清单查询的排序参数取值（服务层与路由照着它们
传参）；`Unread` 是一条会话攒给一个人的数，`ListedRoom` 是一个频道摆上清单时要读的
那几列。四样都是纯值：没有 session、没有懒加载，读得到什么这里就列着什么。

定义留在这里，名字由包的 `__init__.py` 再导出 —— 调用方读的仍是
`app.domain.topic.repositories.Unread` 这一类路径。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.domain.topic.models import TopicStatus

TopicSortField = Literal["updated_at", "title", "last_activity_at"]

SortOrder = Literal["asc", "desc"]


@dataclass(frozen=True)
class Unread:
    """What one conversation has waiting for one person: the number on it,
    whether its name is bold, and how many messages came since they last read
    it (where the 「新消息」 line goes when they open it)."""

    count: int
    new: bool
    messages: int


@dataclass(frozen=True, slots=True)
class ListedRoom:
    """一个频道摆上清单时要读的那几列 —— 一份纯值。

    「待我处理」把几百个频道收窄到真正要摆的那几个之后，读它们读的是标题、状态、日期
    这几位，而 `Topic` 那一行还背着环境文档（`environment` / `default_environment` 两
    列 JSON）和简介。整行装配一行比读这几列贵得多（同 `room_task.ListedTask`），所以
    这一份是**列投影交出来的值**，没有 session、没有懒加载：读得到什么，这里就列着
    什么。

    `status` 走 `TopicStatus` 而不是 `str`：这一列在模型上就是那个枚举，调用方拿它做
    词比较（`room_presentation` 里 `facts.status == TopicStatus.archived`）。
    """

    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    status: TopicStatus
    accepted_at: datetime | None
    updated_at: datetime
