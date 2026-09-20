"""一条事件「关于什么」，以及它因此落在哪里（结论 14，不变量 I10）。

事件从前没有「关于什么」这一栏：每个产生事件的调用点自己挑一个 `topic_id`（有时
再挑一个 `task_id`），于是同一个问题在十几处各答了一遍，没有一处答得出别处的答案
是什么。**落点不是每个调用点的自由，是一张封闭表上的三行**：

| 事件关于 | 落在哪 | 例子 |
|---|---|---|
| 一件活 | 那张卡（房间 + `task_id`） | 检查红了、上游冲突、这一轮换了模型 |
| 一个房间 | 房间时间线（`task_id` 空） | 机器离线、成员加入、有人说话 |
| 一个项目 | 项目总览（`TopicKind.root` 那个房间） | 巡检到点、名册变化 |

调用点改说**它关于什么**，落点由 `landing()` 给。三档是封闭的：多出第四种事件时
这里会缺一行，而不是某个调用点又自己挑一个房间。

**架在现有的两列上，不新开列。**「关于什么」能从 `topic_id` / `task_id` 推出来
（有 `task_id` 就是卡的事，没有就是房间的事），再存一份 `about_kind` + `about_id`
就是同一个事实的第二份声明：两份一旦对不上，没有哪一份是对的。所以这里只有一张表
和一个函数，`blocks` 一列不加，一条迁移不写。

**总览是哪个房间，这里不校验。**项目那一档要的房间由调用点从
`Project.root_topic_id` 取；`landing()` 是个纯函数，读不到项目行，也就无从分辨递
进来的是不是 `TopicKind.root` 那个房间。这张表答的是「这条事件关于什么」，答不了
「你给的房间对不对」——那一句由调用点负责，就在它取 `root_topic_id` 的那一行上。
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass


class EventAbout(enum.StrEnum):
    """一条事件关于的那个东西。三档，封闭。"""

    task = "task"
    room = "room"
    project = "project"


@dataclass(frozen=True)
class Landing:
    """一条事件的落点：写进 `blocks` 的那三个 id。"""

    project_id: uuid.UUID
    topic_id: uuid.UUID
    task_id: uuid.UUID | None


def landing(
    about: EventAbout,
    *,
    project_id: uuid.UUID,
    room_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> Landing:
    """按「关于什么」给出落点。

    缺 id 就抛 `ValueError`：一条关于某张卡的事件拿不出卡号，落到房间时间线上是
    悄悄落错地方——它在卡上再也读不到，而没有任何一处会响。
    """
    match about:
        case EventAbout.task:
            if room_id is None or task_id is None:
                raise ValueError("卡的事要有房间和卡：room_id 与 task_id 都不能空")
            return Landing(project_id=project_id, topic_id=room_id, task_id=task_id)
        case EventAbout.room:
            if room_id is None:
                raise ValueError("房间的事要有房间：room_id 不能空")
            if task_id is not None:
                raise ValueError("房间的事不落在卡上：带了 task_id 就该说 task")
            return Landing(project_id=project_id, topic_id=room_id, task_id=None)
        case EventAbout.project:
            if room_id is None:
                raise ValueError(
                    "项目的事落项目总览：room_id 要给 `Project.root_topic_id` 那个房间"
                )
            if task_id is not None:
                raise ValueError("项目的事不落在卡上：带了 task_id 就该说 task")
            return Landing(project_id=project_id, topic_id=room_id, task_id=None)
