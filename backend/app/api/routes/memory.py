"""Memory routes — 记忆可见 (spec §8.4).

芝士 writes memories as files (`~/.cheese/memory/`, one markdown file per memory),
and humans must be able to SEE (and prune) what it remembers, or the memory is a
black box. 这一条路由就是那一面：读 `memory_files` 那棵树（`files_store.py`），
不是上一版的条目池。

看的是两棵：**项目共享的那一份**（`team`，项目里谁写的就是谁的），加上**问的人
自己那一份**（`private/<handle>`，跟人跟项目走）。别人的 private 不在这里——它属于
那个人，本项目管理员在记忆文件那条路上看得见是为了出事时能查，而「芝士认为我是谁」
这一页不是那种场合。

条目池（`memory_entries`）已经没有写入方了，而且它那一份既不注入提示词、也没有读点：
再照着它列一遍，读到的是一屏「暂无记忆」，而树里明明写着几十条。修剪同理——删的是
那棵树里的文件，连同索引里指向它的那一行（`MemoryFileStore.forget`），不然下一轮
注入的索引会指着一个不存在的文件。
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.memory.files import (
    INDEX_NAME,
    MemoryFileError,
    MemoryFileScope,
    parse_memory_file,
)
from app.domain.memory.files_store import MemoryFileStore
from app.domain.memory.models import MemoryFileRecord

router = APIRouter(prefix="/memory", tags=["memory"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _entry_out(row: MemoryFileRecord) -> dict:
    """一条记忆，照人读的形状给。

    给的是**正文**：文件头上那三行 frontmatter（`name` / `description` / `type`）
    是给召回器和同步用的，摆在这一页上就是一段谁都不想读的元数据。读不成记忆文件的
    正文（格式有问题的那种，写入时只警告不拒绝）就原样给——这一页的用处正是让人
    看见「它记了什么」，而看不出来的一版也该看得见。
    """
    try:
        content = parse_memory_file(row.content).body
    except MemoryFileError:
        content = row.content.strip()
    return {
        "id": str(row.id),
        "scope": row.scope.value,
        "owner_handle": row.owner_handle or None,
        "path": row.path,
        "content": content,
        "version": row.version,
        "updated_by": row.updated_by,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@router.get("")
async def list_memory(
    db: DbSession,
    resolver: ActorResolverDep,
    project_id: uuid.UUID,
    user_handle: str | None = None,
) -> dict:
    """这个项目这棵树上的记忆，改过的靠前。

    ``user_handle`` 问的是另一个问题——**我自己那几条**。private 那棵树是「人 ×
    项目」：某一个人在这个项目里的那一份，属于那个人，别人（连项目管理员也一样）
    读不到，所以这个参数只认本人。不传就只列项目共享的那一份：一个刚进项目的队友
    打开这一页，看到的是项目记得的事，不是别人私下的偏好。

    索引（`MEMORY.md`）不在这里：它是「有哪些条」的目录，不是一条记忆。
    """
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    if user_handle and user_handle != actor.handle:
        raise ForbiddenError(say("memoryOwnOnly"))
    store = MemoryFileStore(db)
    wanted: list[tuple[MemoryFileScope, str | None]] = [(MemoryFileScope.team, None)]
    if user_handle:
        wanted.append((MemoryFileScope.private, actor.handle))
    rows = [
        row
        for scope, owner in wanted
        for row in await store.list(project_id, scope, owner)
        if row.path != INDEX_NAME
    ]
    # 改过的那条排前面：这一页是给人扫的，而「最近它记了什么」是扫它的人的问题。
    # 时间戳是同一台钟（`updated_at` 由数据库写），所以比字符串就等于比时刻。
    entries = sorted(
        (_entry_out(row) for row in rows), key=lambda e: e["updated_at"], reverse=True
    )
    return ok(page(entries, len(entries)))


@router.delete("/{entry_id}")
async def delete_memory(
    entry_id: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """人工修剪一条记忆 (curation, not data loss — the tree is a projection).

    一条记忆由「列得出它的人」删：项目共享的那一份，读得到项目的人就删得动；
    关于某个人自己那一份，只有本人。别人一律答「这条不存在」，和从没存在过的 id
    答同一句话——一个答得不一样的拒绝，等于把这个 id 告诉了不该知道它的人。

    删的是文件**加索引里指着它的那一行**：只删文件的话，下一轮注入的索引里还挂着
    一条指向不存在文件的指针，读起来像「这条记忆在」。
    """
    actor = await resolver.require_verified_caller()
    try:
        row_id = uuid.UUID(entry_id)
    except ValueError as exc:
        raise ValidationError(say("memoryEntryIdInvalid")) from exc
    missing = NotFoundError(say("memoryEntryNotFound"))
    entry = await db.get(MemoryFileRecord, row_id)
    if entry is None or entry.path == INDEX_NAME:
        raise missing
    try:
        await resolver.authorize_project(actor, project_id=entry.project_id)
    except (ForbiddenError, NotFoundError) as exc:
        raise missing from exc
    if entry.scope is MemoryFileScope.private and entry.owner_handle != actor.handle:
        raise missing
    await MemoryFileStore(db).forget(
        project_id=entry.project_id,
        scope=entry.scope,
        owner_handle=entry.owner_handle,
        paths=[entry.path],
        updated_by=actor.handle,
    )
    return ok({"deleted": entry_id})
