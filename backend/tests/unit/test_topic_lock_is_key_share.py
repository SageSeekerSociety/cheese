"""频道的锁要真的是共享锁：``FOR KEY SHARE``，不是 ``FOR NO KEY UPDATE``。

归档和 work-lease 都靠 ``TopicRepository.lock`` 锁住频道那一行。要的是 KEY
SHARE：它挡住归档这种独占写，但不挡住只带外键的挂钩写入；同一房间的两个执行准入
写的是**各自会话**的行，会话行的 ``FOR UPDATE`` 才是那个串行点，所以它们不该在这
把锁上互相排队。

SQLAlchemy 在 ``read=False``（默认）时把 ``key_share=True`` 渲染成 ``FOR NO KEY
UPDATE``——那是排他锁，和自己冲突。也就是说「注释里写了 KEY SHARE」和「数据库真的
取 KEY SHARE」是两件事。这两条钉住编译出来的那个词。

不连数据库：锁的强弱只在渲染出来的 SQL 里看得到，要真的量它得开两个连接压同一行。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import cast

from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.topic.repositories import TopicRepository


class _ScalarRows:
    def __init__(self, rows) -> None:
        self._rows = rows

    def one_or_none(self):
        return next(iter(self._rows), None)

    def all(self):
        return list(self._rows)


class _CapturingSession:
    """只做 `lock` 和 `lock_all_in_project` 问会话要的那一件事：`scalars`。

    语句被拦下来编译，而不是发去数据库——这里要看的正是它编译成什么锁子句。
    """

    def __init__(self) -> None:
        self.statements = []

    async def scalars(self, statement, *args, **kwargs):
        self.statements.append(statement)
        return _ScalarRows([])


def _compiled() -> str:
    session = _CapturingSession()
    repo = TopicRepository(cast(AsyncSession, session))
    asyncio.run(repo.lock(uuid.uuid4()))
    (statement,) = session.statements
    return str(statement.compile(dialect=postgresql.dialect()))


def test_the_room_lock_is_key_share_not_key_update() -> None:
    sql = _compiled()

    assert "FOR KEY SHARE" in sql, f"频道锁不是共享锁：{sql}"
    assert "FOR NO KEY UPDATE" not in sql, (
        f"频道锁排他了，同房间的执行准入会互相等：{sql}"
    )


def test_the_project_wide_lock_is_the_same_key_share_lock() -> None:
    """``lock_all_in_project`` 自称「按 lock 锁一间的办法锁每一条」，也得对。

    归档整个项目走的是这条；这里退化成排他锁，归档就会挡住同一项目里所有只带外键
    的写入。
    """
    session = _CapturingSession()
    repo = TopicRepository(cast(AsyncSession, session))
    asyncio.run(repo.lock_all_in_project(uuid.uuid4()))
    (statement,) = session.statements
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "FOR KEY SHARE" in sql, f"项目级频道锁不是共享锁：{sql}"
    assert "FOR NO KEY UPDATE" not in sql, (
        f"项目级频道锁排他了，归档会挡住全项目的挂钩写入：{sql}"
    )
