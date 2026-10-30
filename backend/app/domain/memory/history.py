"""记忆的改动记录：读、恢复、到期清掉。

记录本身是存取层写的（`MemoryFileStore._record`）：每一条改记忆的路都经过它，所以
这里没有「记一条」——只有读回来的那几件事。

**谁读得到一条记录，和谁读得到那条记忆是同一条规矩。** team 的改动项目里读得到
team 记忆的人都读得到；某个人 private 的改动只有他本人，管理员也不例外
（`routes/memory_files._readable`）。门在路由上，和读记忆文件的那一道是同一个函数。

**恢复是一次普通的写入。** 把那一次改动之前的正文按当前版本写回去（`write`，
`writer=restore`），于是它和别的写入走同一道上限、同一道冲突判据，也同样留下一条
新的记录——恢复本身也是能被再撤销的一次改动。
"""

import uuid
from datetime import UTC, datetime, timedelta
from difflib import unified_diff

from sqlalchemy import delete, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.memory.files import (
    INDEX_NAME,
    MemoryFileScope,
    parse_index,
)
from app.domain.memory.files_store import MemoryFileStore
from app.domain.memory.models import MemoryFileChange, MemoryFileRecord
from app.domain.memory.writers import MemoryWriter

#: 一条改动记录留多久。按时间而不是按「每个文件留几版」，是因为这里存的是整份
#: 正文：一个人删掉一条写错了的 private 记忆（比如里面有密钥），按版数保留的话，
#: 那份正文会一直躺在一个再也不改的文件的历史里；按时间，30 天后它就真的没了。
#: 30 天够覆盖「这几周整理是不是删错了什么」这种发现得晚的错——面板上这一栏本来
#: 就叫「最近改动」。
RETENTION = timedelta(days=30)

#: 面板一页读多少条。保留期内的每一条都翻得到（`changes` 的 ``before``），这一条
#: 只是不让一个改动很密的项目一次把一个月的记录全倒给页面。
PAGE = 50

#: 翻页的位置：上一页最后一条的 `(created_at, id)`。同一个事务里的几条改动
#: `created_at` 相同（`now()` 是事务开始的时刻），所以要带上 id 才排得出先后。
Cursor = tuple[datetime, uuid.UUID]


async def changes(
    session: AsyncSession,
    project_id: uuid.UUID,
    scope: MemoryFileScope,
    owner_handle: str | None,
    *,
    before: Cursor | None = None,
    limit: int = PAGE,
) -> tuple[list[MemoryFileChange], bool]:
    """一个作用域的改动，新的在前，一页 ``limit`` 条；``before`` 是上一页最后
    一条的位置。返回这一页和后面还有没有。谁能读由调用方先判过。"""
    query = select(MemoryFileChange).where(
        MemoryFileChange.project_id == project_id,
        MemoryFileChange.scope == scope,
        MemoryFileChange.owner_handle
        == ("" if scope is MemoryFileScope.team else (owner_handle or "")),
    )
    if before is not None:
        query = query.where(
            tuple_(MemoryFileChange.created_at, MemoryFileChange.id)
            < tuple_(literal(before[0]), literal(before[1]))
        )
    rows = list(
        (
            await session.scalars(
                query.order_by(
                    MemoryFileChange.created_at.desc(), MemoryFileChange.id.desc()
                ).limit(limit + 1)
            )
        ).all()
    )
    return rows[:limit], len(rows) > limit


def cursor_of(row: MemoryFileChange) -> Cursor:
    return row.created_at, row.id


async def change(
    session: AsyncSession, change_id: uuid.UUID
) -> MemoryFileChange | None:
    return await session.get(MemoryFileChange, change_id)


def diff_of(row: MemoryFileChange) -> str:
    """这一次改动的统一 diff（改之前 → 改之后），给面板画。"""
    old = (row.before or "").splitlines(keepends=True)
    new = (row.after or "").splitlines(keepends=True)
    return "".join(
        unified_diff(old, new, fromfile=row.path, tofile=row.path, n=1, lineterm="\n")
    )


async def restore(
    session: AsyncSession, row: MemoryFileChange, *, by: str
) -> MemoryFileRecord:
    """把这条记忆写回这一次改动之前的样子。调用方已判过 ``by`` 写得动这个作用域。

    这一次是新建的（改之前什么都没有）不恢复：撤销一次新建就是删掉它，那是面板
    上本来就有的删除。

    恢复一条被删掉的记忆时，索引里指着它的那一行也一起补回来（被删时 `forget`
    把那一行一并拿掉了）：只回来一个文件、索引里没有它，下一轮注入的索引里就没
    有这条记忆，芝士读不到它，等于没恢复。补回来的是原来那一行，一字不改
    （`_removed_index_line`）。
    """
    if row.before is None:
        raise ValidationError(say("memoryChangeWasCreation", path=row.path))
    store = MemoryFileStore(session)
    owner = row.owner_handle or None
    current = await store.get(
        row.project_id, row.scope, owner, row.path, for_update=True
    )
    if current is not None and current.content == row.before:
        return current
    written = await store.write(
        project_id=row.project_id,
        scope=row.scope,
        owner_handle=owner,
        path=row.path,
        content=row.before,
        updated_by=by,
        expected_version=None if current is None else current.version,
        writer=MemoryWriter.restore,
    )
    if row.path != INDEX_NAME:
        await _point_index_at(session, store, row, by=by)
    return written


async def _point_index_at(
    session: AsyncSession, store: MemoryFileStore, row: MemoryFileChange, *, by: str
) -> None:
    """把删掉 ``row.path`` 时从索引里拿掉的那一行补回末尾；索引里已经有指向它的
    行，或者它被删时索引里本来就没有它，什么都不补。"""
    owner = row.owner_handle or None
    index = await store.get(
        row.project_id, row.scope, owner, INDEX_NAME, for_update=True
    )
    text = index.content if index is not None else ""
    if any(entry.path == row.path for entry in parse_index(text)):
        return
    line = await _removed_index_line(session, row)
    if line is None:
        return
    if text and not text.endswith("\n"):
        text += "\n"
    await store.write(
        project_id=row.project_id,
        scope=row.scope,
        owner_handle=owner,
        path=INDEX_NAME,
        content=f"{text}{line}\n",
        updated_by=by,
        expected_version=None if index is None else index.version,
        writer=MemoryWriter.restore,
    )


async def _removed_index_line(
    session: AsyncSession, row: MemoryFileChange
) -> str | None:
    """删掉 ``row`` 那条记忆的同时从索引里拿掉的、指向它的那一行，原样。

    索引的每一次改动也在这张表里，删记忆的 `forget` 和拿掉那一行在同一个事务、
    同一个 `created_at`。所以找的是这次删除那一刻或之后、索引里第一次拿掉指向
    它的行的那条记录，取它改之前的那一行。不从记忆正文重新生成：那一行的标题
    是写它的人挑的，重新生成的未必是原样，也未必放得进一行的上限。
    """
    edits = await session.scalars(
        select(MemoryFileChange)
        .where(
            MemoryFileChange.project_id == row.project_id,
            MemoryFileChange.scope == row.scope,
            MemoryFileChange.owner_handle == row.owner_handle,
            MemoryFileChange.path == INDEX_NAME,
            MemoryFileChange.created_at >= row.created_at,
        )
        .order_by(MemoryFileChange.created_at, MemoryFileChange.id)
    )
    for edit in edits:
        kept = {entry.path for entry in parse_index(edit.after or "")}
        if row.path in kept:
            continue
        for line in (edit.before or "").splitlines():
            if any(entry.path == row.path for entry in parse_index(line)):
                return line
    return None


async def purge_expired(sessions: SessionFactory) -> int:
    """删掉超过保留期的改动记录（周期任务 `memory history retention`）。返回几条。"""
    async with sessions() as session:
        result = await session.execute(
            delete(MemoryFileChange).where(
                MemoryFileChange.created_at < datetime.now(UTC) - RETENTION
            )
        )
        await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


#: 路由读一条记录用的类型名：路由不读模型（架构守卫 C2），从这里拿。
Change = MemoryFileChange
