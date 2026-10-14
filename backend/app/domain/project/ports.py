"""本包够不着的那些事，写在这里由调用方递进来。

两件都不是本包做得成的，都在第 5 层的 `agent`、`agent_instance` 里
（`docs/manual/dev/architecture.md` 的层表），所以本包只写下自己要的那两次调用，
谁在门口谁把它递进来 —— 和 `webhook/ports.py` 的 `Poster`、
`integration/ports.py` 的 `RoomFiles` 同一条规矩：口子按消费者分开写，实现按提供者
放（`app/domain/agent_instance/adapters.py`）。

返回值是不透明的：``announce()`` 交回它写下的那个 ``Block``，而 ``block`` 也在本包
上面。本包这里不关心落下了哪一行，只声明**可能什么都没落**（房间没了）。
"""

import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.models import Project


class Announce(Protocol):
    """把一句话落进一个房间、按 `meta` 投出去。"""

    async def __call__(
        self,
        session: AsyncSession,
        *,
        place_id: uuid.UUID,
        task_id: uuid.UUID | None,
        content: str,
        meta: dict | None,
    ) -> object | None:
        """Returns what it landed, or ``None`` if the place is gone."""
        ...


class DefaultTeammate(Protocol):
    """这个项目的默认 AI 队友：还没有就播一个，人设按调用方说的落上去。

    项目的芝士和它在总览里的席位，与项目同一个事务里出生（结论 4、不变量 I9b）：
    一个项目不会有「还没有 agent」的那一刻。播种是全仓唯一那一处，在
    `AgentInstanceService.materialize_default` 里，本包够不着。

    一次调用答三件事：把实例播出来、给它一个名字（``display_name``）、给它一份人设
    （``type_name``）。传的是**那一行 Project 本身**而不是它的 id：播种会就地写回
    ``default_agent_instance_id``，也要读它上面的 ``root_topic_id`` 才坐得下席位。
    人设只在它**还没有**的时候落 —— 一个项目自己挑过的人设不该被后来的一次赛题报名
    覆盖；建项目那一次实例刚出生，本来也没有人设，所以那一条路不受这条规矩影响。

    形状就是 `AgentInstanceService.materialize_default` 加它的 `set_type`，改完就地
    flush，调用方不用自己刷。
    """

    async def __call__(
        self,
        session: AsyncSession,
        project: Project,
        *,
        display_name: str | None = None,
        type_name: str | None = None,
    ) -> None: ...
