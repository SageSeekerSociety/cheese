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
from types import SimpleNamespace

import pytest
from sqlalchemy.sql.util import find_tables

from app.domain.repository import identity, task_rows
from app.domain.room_task.models import Task, TaskStatus

#: 裸表 → 它抄的那张真表。
REAL = Task.__table__

#: `repository` 读到的列。副本只声明这些：少一列，只有读到它的那个请求才会炸。
READ_COLUMNS = {
    "id",
    "project_id",
    "room_id",
    "status",
    "title",
    "branch_name",
    "base_branch",
    "pr_number",
    "delivered_head",
    "closing_since",
    "owner_handle",
    "reporter_handle",
    "created_by",
    "author_handle",
    "contributor_handles",
    "created_at",
}


def test_every_declared_column_is_a_column_of_the_real_table() -> None:
    """副本上的每个列名，真表上都得有：真表改了名，这条在本地就红。"""
    real = {column.name for column in REAL.columns}
    unknown = sorted(set(task_rows._tasks.c.keys()) - real)
    assert not unknown, (
        f"task_rows 声明了 Task 上没有的列：{unknown}。"
        "真表改了列名时，抄在这儿的那份要跟着改。"
    )


def test_the_copy_declares_every_column_the_readers_take() -> None:
    """副本只声明这一包读的列；少一列，只有读到它的那个请求才会 500。

    `topic` 那边每条读法都把列写进语句，少一列在建语句时就炸；这里的读法是
    `select(_tasks)`，列在运行时按属性取 —— 删掉一列，单测全绿。所以把清单钉死：
    读者改了要读的列，这里和 `task_rows` 一起改。
    """
    assert set(task_rows._tasks.c.keys()) == READ_COLUMNS


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

    `check_columns=True` 也不能省：`find_tables` 默认只收 `FROM` / `JOIN` 上的表
    节点，`WHERE`、`ORDER BY` 里单独出现的列不算。于是「表还用着副本、排序却拿了
    `Task.created_at`」这种退法，不开这个开关照样绿。
    """
    # 开了 `check_columns`，没有所属表的列（字面量、`label`）会交回 `None`，跳过。
    tables = [t for t in find_tables(statement, check_columns=True) if t is not None]  # type: ignore[arg-type]
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
            table is task_rows._tasks
            for table in find_tables(statement)  # type: ignore[arg-type]
        ), "这两条读法不再从 task_rows 里那份裸表取行了"


async def test_the_attribute_style_still_works_on_the_copy() -> None:
    """读法用属性取列（`_tasks.c.status`）—— 换成 `.c.` 取不出的写法，这里当场红。

    `topic` 那份 2026-10-16 栽过一次（别名取列只能写 `.c.`，属性写在建语句时报
    AttributeError），而那个错只在真库上才跑得到。这条在本地预演一次。
    """
    session = _AnswersNothing()
    await task_rows.one(session, uuid.uuid4())  # type: ignore[arg-type]
    await task_rows.many(session, [uuid.uuid4(), uuid.uuid4()])  # type: ignore[arg-type]
    one, many = (_sql(statement) for statement in session.seen)
    # 单条读法钉住 `WHERE`：少了这一句，`one` 会随表里第一行漂走。
    assert "WHERE tasks.id = " in one, one
    assert "tasks.id IN" in many, many
    # 排序的第二个键也要钉：只按 `created_at` 排，同一毫秒落库的两行顺序不定，
    # `work_items` 的署名顺序就跟着不定。
    assert "ORDER BY tasks.created_at, tasks.id" in many, many


def test_is_open_asks_only_the_status_column() -> None:
    """`is_open` 只判 `status` 一列，比的是那个短码。"""
    assert task_rows.is_open(TaskStatusRow(status=TaskStatus.open.value))
    assert not task_rows.is_open(TaskStatusRow(status=TaskStatus.closed.value))


class TaskStatusRow:
    """只有 `status` 一列的行 —— `is_open` 不该碰别的列。"""

    def __init__(self, *, status: str) -> None:
        self.status = status


class _Row:
    """一行只带副本声明过的列 —— `identity` 多读一列就 AttributeError。"""

    def __init__(self, **values: object) -> None:
        assert set(values) == READ_COLUMNS
        self.__dict__.update(values)


async def test_identity_reads_only_columns_the_copy_declares(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`identity` 从这行上只取副本声明过的列，`_thread` 那条读法也走一遍。

    真的行是 `select(_tasks)` 交回的 `Row`，只带副本声明的列；这里换成同样只有
    这些属性的替身，让「读者多读了一列」在本地红，而不是等那个请求 500。
    `_thread` 从 `TaskService.get` 换过来之后，原来没有单测经过它。

    每一列都要有一条断言经过它：`getattr(task, ..., None)` 会把读错的列悄悄变成
    `None`，只走一遍而不看结果，读者改坏列名照样绿。`author_handle` 只在
    `attribution` 里读，而 `attribution` 要连会员和项目服务，这里不碰。
    """
    task_id = uuid.uuid4()
    creator_id = uuid.uuid4()
    values = {name: None for name in READ_COLUMNS}
    values.update(
        id=task_id,
        title="把后端领域包之间的循环依赖降到零",
        owner_handle="wangchangxin",
        reporter_handle="reporter-x",
        contributor_handles=["cheese-7bfbc959f100"],
    )
    rows = {
        task_id: _Row(**values),
        # 没有 owner，署名要落到 `created_by` 那一列。
        creator_id: _Row(
            **{
                **values,
                "id": creator_id,
                "owner_handle": None,
                "created_by": "creator-x",
            }
        ),
    }

    async def one(_session, wanted):
        return rows.get(wanted)

    async def many(_session, wanted):
        return [rows[wanted_id] for wanted_id in wanted if wanted_id in rows]

    monkeypatch.setattr(task_rows, "one", one)
    monkeypatch.setattr(task_rows, "many", many)

    items = await identity.work_items(
        object(),
        SimpleNamespace(delivered_task_ids=[str(task_id)]),  # type: ignore[arg-type]
    )
    assert [item.task_id for item in items] == [task_id]
    assert items[0].title == "把后端领域包之间的循环依赖降到零"
    assert items[0].reporter_handle == "reporter-x"
    assert items[0].contributor_handles == ("cheese-7bfbc959f100",)

    topic = SimpleNamespace(id=uuid.uuid4(), created_by="someone")
    assert (
        await identity.requester_handle(
            object(),
            topic,
            task_id=task_id,  # type: ignore[arg-type]
        )
        == "wangchangxin"
    )
    assert (
        await identity.requester_handle(
            object(),
            topic,
            task_id=creator_id,  # type: ignore[arg-type]
        )
        == "creator-x"
    )
