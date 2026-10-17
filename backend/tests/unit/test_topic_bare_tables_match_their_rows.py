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

第二半里「用的是不是那三份裸表」按**对象**比，不按表名或 SQL 文本里的子串 —— 退回
ORM 模型时表名一模一样（`Block.__tablename__` 也是 `blocks`），文本比抓不住那一步。
那五条读法建出来的**每一条**语句都过这一关，不只后头单独钉的那两条。
"""

from __future__ import annotations

import uuid

from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.util import find_tables

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

#: 那三张真表本身。本包只该拿上面的副本取行，不该有任何一个出现在语句里 ——
#: 出现就是够回了模型，也就是向上边。
FOREIGN_TABLES = tuple(model.__table__ for _, _, model in COPIES)


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

    borrowed = sorted({name for s in session.seen for name in _borrowed_models(s)})
    assert not borrowed, (
        f"这几条读法够回了别人家模型的真表：{borrowed}。`topic` 在第 2 层，`block` /"
        "`thread` / `room_task` 都在更外 —— 用它们的模型就是把那几条向上边接回来，"
        "而这一档清的正是它们。逐条查在这里，不是只查后头单独钉的那两条。"
    )


def _tables_in(statement: object) -> list[object]:
    """这条语句由哪几张表拼出来，按**调用那一份对象**给出。

    `find_tables` 把语句里每一处 `FROM` / `JOIN` 的元素原样交出来，于是「用的是
    `statements.py` 里那份裸表」比得出来（裸表是 `TableClause`，`is` 一比就是它）
    —— 光看名字比不出来：两者都叫 `blocks`，SQL 文本里长得一样。反过来的那一头
    （够回了别人家模型的真表）不能这么比，见 `_borrowed_models`。
    """
    return list(find_tables(statement))  # type: ignore[arg-type]


def _borrowed_models(statement: object) -> list[str]:
    """这条语句够回了哪几张别人家模型的真表 —— 按对象比，不按表名。

    退回模型时语句里会多出别人家真表这一份，而 `_mentions` 这类判据闭包里还留着
    裸表，所以只断「裸表出现过」抓不住那一步（照样绿）。这一条断的是反面：那几张
    真表一份都不该出现 —— 它们正是这一档要清掉的向上边的落点。

    比的是这些表的**列**属于谁，不是 `find_tables` 交回的那张表：ORM 语句里它交回
    的是带注解的副本（`AnnotatedTable`），和 `Block.__table__` 不是同一个对象，
    `is` 比不上 —— 直接拿表对象比，这条断言对「退回模型」这一类改动永远不红。
    """
    columns = [c for table in _tables_in(statement) for c in table.c]  # type: ignore[attr-defined]
    return [t.name for t in FOREIGN_TABLES if any(c.table is t for c in columns)]


def test_the_replied_to_me_statement_still_reads_the_bare_block_table() -> None:
    """那条缓存的语句仍拿裸表取行 —— 没被换回 `block` / `thread` 的模型。"""
    statement = replied._replied_to_me_statement("user-1")
    tables = _tables_in(statement)
    assert any(t is _blocks for t in tables), (
        "「回复了我」不再从 statements 里那份裸表 blocks 取行了"
    )
    assert any(t is _threads for t in tables), "那条语句不再连裸表 threads"
    borrowed = _borrowed_models(statement)
    assert not borrowed, (
        f"这条语句又够回了别人家模型的真表：{borrowed}。"
        "`topic` 在第 2 层，`block` 在第 4 层、`thread` 更外 —— 用它们的模型就等于"
        "把那几条向上边接回来，而这一档清的正是它们。"
    )


async def test_the_unread_badge_still_reads_the_bare_task_table() -> None:
    """未读角标那条仍按裸表连 `tasks` —— 这是 `topic -> room_task` 那一读的落点。"""
    repo, session = _session_and_repo()
    await repo.unread_counts(uuid.uuid4(), "user-1")
    joins_tasks = [s for s in session.seen if any(t is _tasks for t in _tables_in(s))]
    assert joins_tasks, "未读角标没有再连裸表 tasks：那一读被改成了别的东西"
    assert any(any(t is _blocks for t in _tables_in(s)) for s in joins_tasks), (
        "连了 tasks 却没连裸表 blocks：这个 EXISTS 子查询的形状变了。"
    )
    borrowed = [name for s in session.seen for name in _borrowed_models(s)]
    assert not borrowed, (
        f"角标那几条语句又够回了别人家模型的真表：{borrowed}。"
        "`tasks` 的模型在 `room_task`（本包够不着），够回去就是 `topic -> room_task`"
        "那条向上边。"
    )
