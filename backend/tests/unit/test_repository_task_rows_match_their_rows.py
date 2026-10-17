"""`repository` 抄的 `tasks` 几列是 `room_task` 真表的副本 —— 副本会分叉，钉住它。

`task_rows.py` 用 `table(...column(...))` 把要用的列名逐字抄了一遍。这是 C5 的代价：
`room_task` 在第 4 层、`repository` 在第 3 层，本包够不着那个模型，可它读的确实是那
张表 —— 一条活还在不在跑、它的分支和落地的那次交付叫什么、署名那几列是什么。

抄对了 SQL 一样；抄错了不报错 —— 真表改了列名，这里的 `WHERE` 还指着旧名字，单测
全绿，只有连库的集成测试才炸。`tests/unit/test_topic_bare_tables_match_their_rows.py`
是同一张纸的另一半，那条钉 `topic` 抄的三张。

所以这条测试分两半，都不连库：

1. 副本与真表对一遍：列名、表名、`STATUS_OPEN` 和它代表的枚举成员。
2. 那两条读法，把语句**建出来**这件事本身。用一个只收语句的假会话走一遍。
"""

from __future__ import annotations

import uuid

from sqlalchemy.sql.util import find_tables

from app.domain.repository import task_rows
from app.domain.room_task.models import Task, TaskStatus

#: 裸表 → 它抄的那张真表。
REAL = Task.__table__


def test_every_declared_column_is_a_column_of_the_real_table() -> None:
    """副本上的每个列名，真表上都得有：真表改了名，这条在本地就红。"""
    real = {column.name for column in REAL.columns}
    unknown = sorted(set(task_rows._tasks.c.keys()) - real)
    assert not unknown, (
        f"task_rows 声明了 Task 上没有的列：{unknown}。"
        "真表改了列名时，抄在这儿的那份要跟着改。"
    )


def test_the_copy_points_at_the_table_it_copies() -> None:
    """抄的必须是同一张表：表名对不上，等于照着别的表写了 `WHERE`。"""
    assert task_rows._tasks.name == Task.__tablename__


def test_the_open_code_is_the_enum_member_it_stands_for() -> None:
    """`status` 存的是枚举成员名；逐字抄下来的那个码不能和枚举分家。"""
    assert task_rows.STATUS_OPEN == TaskStatus.open.value
    assert task_rows.STATUS_OPEN != TaskStatus.closed.value


class _AnswersNothing:
    """收下每条语句、如实回「一行也没有」：控制流照走，SQL 一行都不发。"""

    def __init__(self) -> None:
        self.seen: list[object] = []

    async def execute(self, statement, params=None):
        self.seen.append(statement)
        return self

    def all(self) -> list:
        return []

    def first(self):
        return None


def _sql(statement: object) -> str:
    """把语句编译一次 —— 建不出来的语句在这儿当场炸。"""
    return str(statement.compile())  # type: ignore[attr-defined]


def _borrowed(statement: object) -> bool:
    """这条语句用的是不是 `room_task` 那张真表 —— 按**对象**比，不按表名。

    两者都叫 `tasks`，SQL 文本里长得一模一样，只有对象分得开。

    比的是表的**列**属于谁，不是 `find_tables` 交回的那张表：ORM 语句里它交回的是
    带注解的副本（`AnnotatedTable`），和 `Task.__table__` 不是同一个对象，`is` 比
    不上 —— 直接拿表对象比，这条断言对「退回模型」这一类改动永远不红。
    """
    tables = find_tables(statement)  # type: ignore[arg-type]
    return any(column.table is REAL for table in tables for column in table.c)


async def test_the_two_reads_build_their_statements() -> None:
    """两条读法各走一遍，并逐条钉住它们取的是裸表、不是别人家的模型。"""
    session = _AnswersNothing()
    task_id = uuid.uuid4()

    await task_rows.one(session, task_id)  # type: ignore[arg-type]
    await task_rows.many(session, [task_id, uuid.uuid4()])  # type: ignore[arg-type]
    # 一个空列表不发语句：`many` 那条早退也是要保住的行为（`work_items` 靠它）。
    assert await task_rows.many(session, []) == []  # type: ignore[arg-type]

    assert len(session.seen) == 2, f"两条读法建出的语句数不对：{len(session.seen)}"
    for statement in session.seen:
        _sql(statement)
    for statement in session.seen:
        assert not _borrowed(statement), (
            "这两条读法够回了 `room_task` 的模型：`repository` 在第 3 层、"
            "`room_task` 在第 4 层 —— 用它的模型就是把那条向上边接回来，"
            "而这一档清的正是它。"
        )
        assert any(
            table is task_rows._tasks for table in find_tables(statement)  # type: ignore[arg-type]
        ), "这两条读法不再从 task_rows 里那份裸表取行了"


async def test_the_attribute_style_still_works_on_the_copy() -> None:
    """读法用属性取列（`_tasks.c.status`）—— 换成 `.c.` 取不出的写法，这里当场红。

    `topic` 那份 2026-10-16 栽过一次（别名取列只能写 `.c.`，属性写在建语句时报
    AttributeError），而那个错只在真库上才跑得到。这条在本地预演一次。
    """
    session = _AnswersNothing()
    await task_rows.many(session, [uuid.uuid4(), uuid.uuid4()])  # type: ignore[arg-type]
    compiled = _sql(session.seen[-1])
    assert "ORDER BY tasks.created_at" in compiled, compiled
    assert "tasks.id IN" in compiled, compiled


def test_is_open_asks_only_the_status_column() -> None:
    """`is_open` 只判 `status` 一列，比的是那个短码。"""
    assert task_rows.is_open(TaskStatusRow(status=TaskStatus.open.value))
    assert not task_rows.is_open(TaskStatusRow(status=TaskStatus.closed.value))


class TaskStatusRow:
    """只有 `status` 一列的行 —— `is_open` 不该碰别的列。"""

    def __init__(self, *, status: str) -> None:
        self.status = status
