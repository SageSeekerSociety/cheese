"""项目那一行里，这一包要读的那一列。

`topic_membership` 问项目的只有一个问题：这个项目的芝士是哪个实例。答它的那一列
是 `projects.default_agent_instance_id`，而读整行的 `ProjectRepository` 在
`project` 包里 —— 两个包都在第 2 层，互相 import 一次就是一个环（C3 的
`domains-acyclic` 查的正是这个）。

所以那一列在这里逐字声明成裸表，和 `topic/repositories/statements.py`、
`repository/task_rows.py` 同一条规矩：**只有读法走这条路**，写和判断仍归
`project` 自己。

抄对了 SQL 一样；抄错了不报错 —— 真表改了列名，这里的 `SELECT` 还指着旧名字，
单测全绿，只有连库的集成测试才炸。`tests/unit/test_project_rows_match_their_row.py`
在本地把它钉住。
"""

from __future__ import annotations

import uuid

from sqlalchemy import Uuid, column, select, table
from sqlalchemy.ext.asyncio import AsyncSession

#: `projects` 的一列副本。列名由上面那条守卫测试对拍。
_projects = table(
    "projects",
    column("id", Uuid),
    column("default_agent_instance_id", Uuid),
)


async def default_agent_instance_id(
    session: AsyncSession, project_id: uuid.UUID
) -> uuid.UUID | None:
    """这个项目默认的 AI 队友是哪个实例，没有就是 None。"""
    return await session.scalar(
        select(_projects.c.default_agent_instance_id).where(
            _projects.c.id == project_id
        )
    )
