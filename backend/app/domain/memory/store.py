"""Memory store — the flat ``memory_entries`` projection in PG.

记忆的正身是会话目录里那棵树（`files.py` / `files_store.py`），这张表是**上一
版**留下的：条目池、core / fact 两层、按「agent × 项目 × 人」分的 scope_id。它
还在，因为库里已有的那些行还得读得到（个人主页上「芝士对我的认识」那一栏、以及旧行
的迁移）。**它现在没有写入方了**：写记忆改成写 `memory_files` 那棵树
（`files_store.py`），最后一个往这里写的工具（`cheese_remember`）也已经撤掉；留着的
`DbMemoryStore.remember` 是给按键写库的地方用的（测试、以及日后整理旧行的活）。

**搜寻那一条路已经撤了。** 关键词检索是「条目池时代」的召回手段：池子里的条目
不在上下文里，所以要有一次查询去够。换成文件之后，索引每轮都在注入里，正文是
会话目录里一个能直接 Read 的文件——再留一个「按关键词找记忆」的命令，等于给同一
件事留两条路，而检索那条总是更差的那条。`recall` / `core_and_counts` / `search`
一并删掉了。
"""

import uuid
from typing import Protocol

from sqlalchemy import ColumnElement, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory.models import (
    MemoryEntry,
    MemoryLayer,
    MemoryScope,
    user_scope_about,
)


def live_entries() -> ColumnElement[bool]:
    """The one clause every read of ``memory_entries`` must carry.

    记忆整理 retires facts instead of deleting them (see MemoryDream), so the
    table holds rows that are deliberately no longer part of the memory. A read
    that forgets this filter does not fail — it quietly reinstates every fact
    芝士 ever decided was wrong, which is worse than never having organized.
    """
    return MemoryEntry.retired_at.is_(None)


def about_person(person_handle: str) -> ColumnElement[bool]:
    """Every live fact any agent, in any project, holds about this person.

    The question a person's own page asks, and the only one that is not about a
    single pool; see :func:`user_scope_about`.
    """
    return and_(
        MemoryEntry.scope == MemoryScope.user,
        MemoryEntry.scope_id.endswith(user_scope_about(person_handle), autoescape=True),
        live_entries(),
    )


async def forget_fact_about(
    session: AsyncSession, *, entry_id: uuid.UUID, person_handle: str
) -> bool:
    """Delete one fact about ``person_handle``; False when there is no such fact.

    A person prunes what was learned about them the way the memory panel
    prunes a pool: the row goes (memory is a projection of the conversation,
    so this is curation, not data loss). Anything that is not a live fact about
    them answers False, exactly like an id that does not exist.
    """
    deleted = await session.execute(
        delete(MemoryEntry)
        .where(MemoryEntry.id == entry_id, about_person(person_handle))
        .returning(MemoryEntry.id)
    )
    return deleted.first() is not None


class MemoryStore(Protocol):
    async def remember(
        self,
        scope: MemoryScope,
        scope_id: str,
        content: str,
        *,
        layer: MemoryLayer = MemoryLayer.fact,
    ) -> None:
        """Persist a new memory fact."""
        ...


class DbMemoryStore:
    """MemoryStore backed by the memory_entries table."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def remember(
        self,
        scope: MemoryScope,
        scope_id: str,
        content: str,
        *,
        layer: MemoryLayer = MemoryLayer.fact,
    ) -> None:
        self._session.add(
            MemoryEntry(scope=scope, scope_id=scope_id, content=content, layer=layer)
        )
        await self._session.flush()


def memory_store(session: AsyncSession) -> MemoryStore:
    return DbMemoryStore(session)
