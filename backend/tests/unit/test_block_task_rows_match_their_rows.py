"""`block` 手抄的 `tasks` 是 `room_task` 真表的副本 —— 副本会分叉，这里钉住它。

`block/task_rows.py` 用 `table(...column(...))` 把那五列逐字抄了一遍：`room_task`
反过来 import 本包，两边互相 import 就是 C3 说的那条环。抄对了 SQL 一样；抄错了不
报错 —— 真表改了列名，这里的 `WHERE` 还指着旧名字，单测全绿，只有连库的集成测试才
炸。

和 `test_repository_task_rows_match_their_rows.py` 与
`test_topic_bare_tables_match_their_rows.py` 同一条做法：不连库，只比列名。真表加一列
不用改这里；真表**改名或删掉**这五列之一，这里当场红。
"""

from __future__ import annotations

from app.domain.block.task_rows import _tasks
from app.domain.room_task.models import Task

#: 副本 → 它抄的那张真表。
COPIES = (("_tasks", _tasks, Task),)


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


def test_both_block_readers_take_the_shared_copy() -> None:
    """两份读者共用一份副本：各自再抄一份，就是同一张表有两个会走散的版本。"""
    from app.domain.block import documents, editing

    assert documents._tasks is _tasks
    assert editing._tasks is _tasks
