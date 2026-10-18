"""房间行上的两把锁各是哪种：准入是 ``FOR SHARE``，房间自己的状态变更是排他的。

``TopicRepository.lock`` 同时服务两件事，而它们要的互斥不一样：

- **执行准入**（``TopicService.lock_for_execution``）之间不该排队：两个准入写的是
  各自会话的行，会话行 ``FOR UPDATE`` 才是它们的串行点。但它必须等一个正在进行的
  归档，否则会从归档前的 ``active`` 状态放下机器。
- **房间自己的状态变更**——归档、取消归档、清理推进——必须互相排他，否则取消归档
  和清理会各自读着旧状态做相反的决定。

PostgreSQL 的行锁冲突表把这两件事分开：``FOR SHARE`` 与自身不冲突，却与普通
``UPDATE`` 取得的 ``FOR NO KEY UPDATE`` 冲突；``FOR KEY SHARE`` 与两者都不冲突，
所以它满足不了准入要等归档那一条。这几条钉住两条语句各自编译出的锁子句。

不连数据库：锁的强弱在这里就是编译出来的那个词；要量真实的排队得开两个连接压同一
行，那是集成测试的活。
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
    """只做这些调用问会话要的那一件事：`scalars`。

    语句被拦下来编译，而不是发去数据库——这里要看的正是它编译成什么锁子句。
    """

    def __init__(self) -> None:
        self.statements = []

    async def scalars(self, statement, *args, **kwargs):
        self.statements.append(statement)
        return _ScalarRows([])


def _sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


def _lock_sql(*, for_admission: bool = False) -> str:
    session = _CapturingSession()
    repo = TopicRepository(cast(AsyncSession, session))
    asyncio.run(repo.lock(uuid.uuid4(), for_admission=for_admission))
    (statement,) = session.statements
    return _sql(statement)


def test_an_admission_takes_a_share_lock_not_the_exclusive_one() -> None:
    """``FOR SHARE``：准入之间不互相等（不与自己冲突），归档的 ``UPDATE``
    （``FOR NO KEY UPDATE``）仍然挡得住它。"""
    sql = _lock_sql(for_admission=True)

    assert "FOR SHARE" in sql, f"准入没取共享锁：{sql}"
    assert "FOR NO KEY UPDATE" not in sql, (
        f"准入取了排他锁，同房间的两个准入会互相等：{sql}"
    )


def test_the_rooms_own_state_changes_keep_the_exclusive_lock() -> None:
    """归档、取消归档、清理推进都走这条：它们必须互相排他，也必须挡住准入。"""
    sql = _lock_sql()

    assert "FOR NO KEY UPDATE" in sql, f"状态变更的锁不是排他的：{sql}"


def test_the_project_wide_lock_is_the_same_exclusive_lock() -> None:
    """``lock_all_in_project`` 自称「按 lock 锁一间的办法锁每一条」。

    归档整个项目走的是这条；它退化成共享锁，项目归档就会和里面的准入交错。
    """
    session = _CapturingSession()
    repo = TopicRepository(cast(AsyncSession, session))
    asyncio.run(repo.lock_all_in_project(uuid.uuid4()))
    (statement,) = session.statements
    sql = _sql(statement)

    assert "FOR NO KEY UPDATE" in sql, f"项目级频道锁不是排他的：{sql}"
