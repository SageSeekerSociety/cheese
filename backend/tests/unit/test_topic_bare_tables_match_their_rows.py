"""`topic` 手抄的三张裸表是别人家真表的副本 —— 副本会分叉，这里钉住它。

`statements.py` 用 `table(...column(...))` 把 `tasks` / `threads` / `blocks` 的列名
逐字抄了一遍。这是 C5 的代价：`block` 在第 4 层、`room_task` 和 `thread` 更外，本包
（第 2 层）够不着它们的模型，可它读的是那几行数据。抄对了 SQL 一样；抄错了不报错 ——
真表改了列名，这里的 `WHERE` 还指着旧名字，单测全绿，只有连库的集成测试才炸。

所以这条测试分两半，都不连库：

1. 副本与真表对一遍：列名、表名、`kind` 的三个短码和它们代表的枚举。
2. 那几条走裸表的读法，把语句**建出来**这件事本身。别名（`_blocks.alias()`）取列只能
   写 `.c.`，属性写法（`root.id`）在建语句时当场 AttributeError —— 2026-10-16 就是这
   么栽的，而它只在真库上才跑得到。用一个只收语句的假会话把这四条读法走一遍，等于在
   本地把它们预演一次。
"""

from __future__ import annotations

import uuid

from sqlalchemy.dialects import postgresql

from app.domain.block.models import Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.room_task.models import Task
from app.domain.thread.models import Thread
from app.domain.topic.repositories import replied
from app.domain.topic.repositories.latest import last_word
from app.domain.topic.repositories.repository import TopicRepository
from app.domain.topic.repositories.statements import (
    KIND_ARTIFACT,
    KIND_EVENT,
    KIND_MESSAGE,
    _blocks,
    _tasks,
    _threads,
)

#: 裸表 → 它抄的那张真表。
COPIES = (
    ("_blocks", _blocks, Block),
    ("_tasks", _tasks, Task),
    ("_threads", _threads, Thread),
)


def test_every_declared_column_is_a_column_of_the_real_table() -> None:
    """副本上的每个列名，真表上都得有：真表改了名，这条在本地就红。"""
    for name, bare, model in COPIES:
        real = {column.name for column in model.__table__.columns}
        unknown = sorted(set(bare.c.keys()) - real)
        assert not unknown, (
            f"{name} 声明了 {model.__name__} 上没有的列：{unknown}。"
            "真表改了列名时，抄在这儿的那份要跟着改。"
        )


def test_each_copy_points_at_the_table_it_copies() -> None:
    """抄的必须是同一张表：表名对不上，等于照着别的表写了 `WHERE`。"""
    for name, bare, model in COPIES:
        assert bare.name == model.__tablename__, name


def test_the_short_codes_are_the_enums_they_stand_for() -> None:
    """`kind` 存的是枚举成员名；逐字抄下来的三个码不能和枚举分家。

    `author_type` 那一列不在这一条里：它不抄，直接 import 第 1 层的
    `app.domain.event_author`，没有会分叉的第二份。
    """
    assert KIND_MESSAGE == BlockKind.message.value
    assert KIND_ARTIFACT == BlockKind.artifact.value
    assert KIND_EVENT == BlockKind.event.value


class _AnswersNothing:
    """收下每条语句、如实回「一行也没有」：控制流照走，SQL 一行都不发。"""

    def __init__(self) -> None:
        self.seen: list[object] = []

    async def execute(self, statement, params=None):
        self.seen.append(statement)
        return self

    async def scalars(self, statement, params=None):
        self.seen.append(statement)
        return self

    async def get(self, entity, ident):
        return None

    async def flush(self) -> None:
        return None

    def all(self) -> list:
        return []

    def first(self):
        return None


def _session_and_repo() -> tuple[TopicRepository, _AnswersNothing]:
    session = _AnswersNothing()
    return TopicRepository(session), session  # type: ignore[arg-type]


def _sql(statement: object, *, literal: bool = False) -> str:
    """把语句编译成 Postgres 的 SQL 文本 —— 建不出来的语句在这儿当场炸。

    `literal=True` 把绑定参数就地渲染成字面量，用来比「问的是不是同一句」。
    """
    return str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True} if literal else {},
        )
    )


def _after_from(statement: object) -> str:
    """语句里 `FROM` 之后的那截。

    两份副本选的列本来就不一样（一边是模型全行，一边只手抄了几个列），拿整个文本
    比一定不等；能比、也必须相等的是 `FROM` 之后的 `WHERE` / `ORDER BY` / `LIMIT` ——
    那才是「取的是不是同一条」。

    编译要带 `literal_binds`：`LIMIT 1` 和 `LIMIT 2` 默认都编成同一个
    `LIMIT %(param_1)s`，两份语句的文本于是永远相等，条数改了也看不出来。
    """
    flat = " ".join(_sql(statement, literal=True).split())
    assert " FROM " in flat, flat
    return flat.split(" FROM ", 1)[1]


async def test_the_copied_query_asks_the_original_s_question() -> None:
    """`last_word` 和 `latest_for_topic` 是同一句 SQL 的两种写法。

    抄对列名只保证语句建得出来：总序、过滤和取几条都可以悄悄改成另一条行，两边
    还都跑得通。所以这里把两份语句 `FROM` 之后的部分比一遍 —— 一份改了，另一份
    没跟着改，这条就红。
    """
    topic = uuid.uuid4()
    original_session = _AnswersNothing()
    copy_session = _AnswersNothing()

    await BlockRepository(original_session).latest_for_topic(topic)  # type: ignore[arg-type]
    await last_word(copy_session, topic)  # type: ignore[arg-type]

    assert _after_from(copy_session.seen[-1]) == _after_from(
        original_session.seen[-1]
    ), (
        "手抄的那条和 `block/history.py` 的 `latest_for_topic` 问的不是同一条行了："
        "两边都还要改一处。"
    )


async def test_the_bare_table_reads_build_their_statements() -> None:
    """四条读法各走一遍：`root.id` 那类错在**建语句**这一步就 AttributeError。"""
    repo, session = _session_and_repo()
    project = uuid.uuid4()
    topic = uuid.uuid4()

    await repo.unread_counts(project, "user-1")
    await repo.private_unread_counts(project, "user-1", seats={})
    await repo.replied_to_me([topic], "user-1")
    await repo.mark_read_many([topic], "user-1")
    await last_word(session, topic)  # type: ignore[arg-type]

    assert session.seen, "一条语句都没建出来：读法没走到建语句那一步"
    for statement in session.seen:
        _sql(statement)


def test_the_replied_to_me_statement_still_reads_the_bare_block_table() -> None:
    """那条缓存的语句仍指着裸表：改动把它换成别人的表，SQL 文本里看得出来。"""
    sql = _sql(replied._replied_to_me_statement("user-1"))
    assert "blocks" in sql
    assert "threads" in sql


async def test_the_unread_badge_still_reads_the_bare_task_table() -> None:
    """未读角标那条仍按裸表连 `tasks` —— 这是 `topic -> room_task` 那一读的落点。"""
    repo, session = _session_and_repo()
    await repo.unread_counts(uuid.uuid4(), "user-1")
    joins_tasks = [s for s in session.seen if "tasks" in _sql(s)]
    assert joins_tasks, "未读角标没有再连裸表 tasks：那一读被改成了别的东西"
    assert any("blocks" in _sql(s) for s in joins_tasks)
