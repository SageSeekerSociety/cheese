"""`topic_membership` 手抄的 `projects` 是别人家真表的副本 —— 副本会分叉，这里钉住它。

`project_rows.py` 用 `table(...column(...))` 把 `projects` 的两列逐字抄了一遍，因为
`topic_membership` 够不着第 2 层 `project` 的模型（反向 import 就是一个环）。抄对了
SQL 一样；抄错了不报错 —— 真表改了列名，这里的 `SELECT` 还指着旧名字，所有单测照绿，
只有连库的集成测试才炸。

和 `test_topic_bare_tables_match_their_rows.py` 与
`test_repository_task_rows_match_their_rows.py` 同一条做法：不连库，只比列名。真表加了
一列不用改这里 —— 副本只声明它读的那两列；真表**改名或删掉**这两列之一，这里当场红。
"""

from __future__ import annotations

from app.domain.project.models import Project
from app.domain.topic_membership.project_rows import _projects

#: 副本 → 它抄的那张真表。
COPIES = (("_projects", _projects, Project),)


def test_every_declared_column_is_a_column_of_the_real_table() -> None:
    """副本上的每个列名，真表上都得有：真表改了名，这条在本地就红。"""
    for name, bare, model in COPIES:
        real = {column.name for column in model.__table__.columns}
        unknown = sorted(set(bare.c.keys()) - real)
        assert not unknown, f"{name} 抄了真表上没有的列: {unknown}"


def test_the_copy_says_the_real_table_name() -> None:
    """表名也要对得上：抄成另一张表的列名，SQL 会静默读到别处。"""
    for name, bare, model in COPIES:
        assert bare.name == model.__tablename__, name
