"""`topic_membership` 手抄的 `projects` 是别人家真表的副本 —— 副本会分叉，这里钉住它。

`project_rows.py` 用 `table(...column(...))` 把 `projects` 的两列逐字抄了一遍，因为
`topic_membership` 够不着第 2 层 `project` 的模型（反向 import 就是一个环）。抄对了
SQL 一样；抄错了不报错 —— 真表改了列名，这里的 `SELECT` 还指着旧名字，所有单测照绿，
只有连库的集成测试才炸。

和 `test_topic_bare_tables_match_their_rows.py` 与
`test_repository_task_rows_match_their_rows.py` 同一条做法：不连库。三半，各自挡一种
抄错法：

1. 副本上的每一列在真表上都有（真表改名、删列，这里红）。
2. 读法点到的每一列副本都声明了（读法写成 `_projects.c.xxx` 而副本没有那一列，这里红）。
3. 读法能把语句建出来（`root.id` 那类属性写法在建语句时就 AttributeError）。

只做第一半不够：那只能证明「抄的列是真的」，证明不了「读的列抄了」。
"""

from __future__ import annotations

import ast
import pathlib
import uuid

from app.domain.project.models import Project
from app.domain.topic_membership import project_rows
from app.domain.topic_membership.project_rows import _projects

#: 副本 → 它抄的那张真表。
COPIES = (("_projects", _projects, Project),)

#: 读法所在的那份文件。
READER = pathlib.Path(project_rows.__file__)


class _AnswersNothing:
    """收下每条语句、回「一行也没有」：控制流照走，SQL 一行都不发。"""

    def __init__(self) -> None:
        self.seen: list[object] = []

    async def execute(self, statement, params=None):
        self.seen.append(statement)
        return self

    async def scalar(self, statement, params=None):
        self.seen.append(statement)
        return None


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


def test_the_reader_only_takes_columns_the_copy_declares() -> None:
    """读法写 `_projects.c.<列>`，副本就得有那一列：少抄一列，读到的是真表的列名。"""
    taken = {
        node.attr
        for node in ast.walk(ast.parse(READER.read_text()))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "c"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "_projects"
    }
    assert taken, "读法里一处 `_projects.c.<列>` 都没有：这条守卫已经空转"
    missing = sorted(taken - set(_projects.c.keys()))
    assert not missing, f"读法取了副本没声明的列: {missing}"


async def test_the_read_builds_its_statement() -> None:
    """读法走一遍：建语句这一步就把抄错的列名、属性写法之类的问题暴露出来。"""
    session = _AnswersNothing()
    assert await project_rows.default_agent_instance_id(session, uuid.uuid4()) is None
    assert session.seen, "一条语句都没建出来：读法没走到建语句那一步"

    statement = session.seen[-1]
    tables = list(statement.get_final_froms())
    assert any(table is _projects for table in tables), (
        "读法没走这份裸表：退回 ORM 模型时表名一模一样，只有按对象比才拦得住"
    )
