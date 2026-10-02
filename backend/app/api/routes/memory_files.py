"""记忆文件的读、写、删 —— 给界面和 agent 用的那一面。

真相在 `memory_files`（见 `app/domain/memory/files_store.py`），这里只是把它露
出来。两条规矩，都是产品决定，不是实现细节：

- **team 是项目里所有人共读共写共删的。** 项目集体的一份状态，谁都得能修；每次
  改动在总览房间留一条带 diff 的记录（回写那条路发），所以「谁删的」追得回来。
- **private 是「本人 + 项目管理员」，而且只有本人写得动。** 一个人的偏好是他自己
  的；管理员看得见是为了在出事时能查（比如说有人把密钥写进去了），但**改不了**
  ——给他一支笔，这一条记忆就有了两个主人，而「这是谁的判断」正是 private 这一
  层唯一要保住的东西。越权写一律 403，两种角色同一个码。

冲突这一路（版本对不上）在 `MemoryFileStore` 里是 409，带着当前版本号回来；这里
不吞它、也不重试——写入方就在现场，重读一次比自动合并两段散文安全。
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import ForbiddenError, ValidationError
from app.domain.membership.services import MemberService
from app.domain.memory.files import (
    INDEX_NAME,
    MemoryFileError,
    MemoryFileScope,
    check_path,
    fit_index,
    parse_memory_file,
)
from app.domain.memory.files_store import MemoryFileMissing, MemoryFileStore
from app.domain.memory.models import MemoryFileRecord

router = APIRouter(prefix="/memory/files", tags=["memory"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _file_out(row: MemoryFileRecord) -> dict:
    return {
        "id": str(row.id),
        "scope": row.scope.value,
        "owner_handle": row.owner_handle,
        "path": row.path,
        "content": row.content,
        "version": row.version,
        "updated_by": row.updated_by,
        "created_at": row.created_at.isoformat(),
        # 回写时对的是这一版；和 `updated_at` 分开说，因为并发那一侧只认版本号
        # ——时间戳在两边是不同的钟。
        "updated_at": row.updated_at.isoformat(),
    }


def _scope_of(raw: str) -> MemoryFileScope:
    try:
        return MemoryFileScope(raw)
    except ValueError as exc:
        raise ValidationError("scope 只能是 team 或 private") from exc


async def _readable(
    db: AsyncSession,
    resolver: ActorResolverDep,
    project_id: uuid.UUID,
    owner: str | None,
) -> str:
    """谁在读，读不读得到，一起答完；返回读的人自己的 handle。

    team：项目里读得到它的人就读得到。private：本人，或者这个项目的管理员。
    别人问起一个 private 作用域时答 403 而不是 404——private 的存在本身不是
    秘密，里面的内容才是，而 404 会让本人也以为自己的记忆不见了。

    **读得到的范围不等于写得动的范围**：写和删另有一道 ``_writable``，private
    那一侧只认本人。
    """
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    if owner is not None and owner != actor.handle:
        if not await MemberService(db).manages(project_id, actor.handle):
            raise ForbiddenError("私人记忆只有本人和项目管理员看得见")
    return actor.handle


async def _writable(
    db: AsyncSession,
    resolver: ActorResolverDep,
    project_id: uuid.UUID,
    owner: str | None,
) -> str:
    """谁在写/删，写不写得动，一起答完；返回动手的人自己的 handle。

    private 只认本人，**管理员也不例外**：管理员看得见是为了出事时能查（比如说
    有人把密钥写进去了），不是为了替谁改。给他一支笔，那一条记忆就同时有了两个
    主人，而「这是谁的判断」正是 private 这一层唯一要保住的东西——查得到就够了。
    越权答 403，和读的那一道同一个码：读得到而写不动，不是「这里没有这条记忆」。

    team 没有这一道：项目集体的一份状态，读得到的人就写得动，改动在总览房间留
    一条带 diff 的记录（回写那条路发），所以「谁删的」追得回来。
    """
    actor = await _readable(db, resolver, project_id, owner)
    if owner is not None and owner != actor:
        raise ForbiddenError("私人记忆只有本人改得动——管理员看得见，但改不了")
    return actor


def _checked_path(raw: str) -> str:
    """路径先在这一层过一关，把它变成一个说得清的拒绝。

    `MemoryFileStore` 也查一次（它是最后一道），但那一次抛的是 `MemoryFileError`
    ——一个 `ValueError`，没有对应的处理器，于是「路径写成了 `../x.md`」会读成服务器
    内部错误。写路径的是 agent 自己，它会照着那句拒绝改。
    """
    path = str(raw or "").strip()
    try:
        return check_path(path)
    except MemoryFileError as exc:
        raise ValidationError(str(exc)) from exc


def _owner_of(scope: MemoryFileScope, owner: str | None) -> str | None:
    """private 必须点名是谁的；team 没有 owner（传了也不看）。"""
    if scope is MemoryFileScope.team:
        return None
    if not owner:
        raise ValidationError("private 记忆必须带 owner_handle")
    return owner


@router.get("")
async def list_memory_files(
    db: DbSession,
    resolver: ActorResolverDep,
    project_id: uuid.UUID,
    scope: str = Query("team"),
    owner_handle: str | None = None,
) -> dict:
    """一个作用域下的全部文件（含索引），带内容和版本号。

    一次性给全，不是分页：一个作用域的记忆就是几十个文件，而界面要拿它画索引
    和条目——分页只会把一个整块的东西切碎又让人拼回去。
    """
    which = _scope_of(scope)
    owner = _owner_of(which, owner_handle)
    await _readable(db, resolver, project_id, owner)
    rows = await MemoryFileStore(db).list(project_id, which, owner)
    return ok(page([_file_out(r) for r in rows], len(rows)))


@router.put("")
async def write_memory_file(
    body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """写入一版。`version` 缺省 = 新建（重名就是冲突，见 `MemoryFileStore.write`）。

    格式不对**不拒绝**，只回一条警告：要是把格式变成闸，第一条记忆就得同时写对
    frontmatter、索引行和路径——三样里错一样就一个字都存不下。**超了单条上限拒绝**
    （422，`MemoryFileStore.write`）：写的人手上就有这一条，当场就改得短。

    谁写得动由 `_writable` 答：team 读得到就写得动，private 只认本人。
    """
    try:
        project_id = uuid.UUID(str(body.get("project_id") or ""))
    except ValueError as exc:
        raise ValidationError("project_id 无效") from exc
    which = _scope_of(str(body.get("scope") or "team"))
    owner = _owner_of(which, (body.get("owner_handle") or "").strip() or None)
    actor = await _writable(db, resolver, project_id, owner)
    path = _checked_path(str(body.get("path") or ""))
    content = body.get("content")
    if not isinstance(content, str):
        raise ValidationError("content 必须是字符串")
    raw_version = body.get("version")
    expected: int | None = None
    if raw_version is not None:
        try:
            expected = int(raw_version)
        except (TypeError, ValueError) as exc:
            raise ValidationError("version 必须是整数") from exc
    store = MemoryFileStore(db)
    if path == INDEX_NAME:
        row = await store.write(
            project_id=project_id,
            scope=which,
            owner_handle=owner,
            path=path,
            content=content,
            updated_by=actor,
            expected_version=None if expected is None else expected,
        )
        _, warning = fit_index(content)
        return ok({"file": _file_out(row), "warning": warning})
    try:
        memory = parse_memory_file(content)
    except MemoryFileError as exc:
        row = await store.write(
            project_id=project_id,
            scope=which,
            owner_handle=owner,
            path=path,
            content=content,
            updated_by=actor,
            expected_version=expected if expected is not None else None,
        )
        return ok({"file": _file_out(row), "warning": str(exc)})
    row = await store.write(
        project_id=project_id,
        scope=which,
        owner_handle=owner,
        path=path,
        content=content,
        updated_by=actor,
        expected_version=expected,
    )
    warning = None
    if memory.description and len(memory.description) > 150:
        warning = "description 超过 150 字符：它只用来判相关性，长的那部分放正文里。"
    return ok({"file": _file_out(row), "warning": warning})


@router.post("/delete")
async def delete_memory_file(
    body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """删掉一条记忆。

    路径是索引时必须一起点出被它指着的那一条也删了没有 —— 平台不替写入方推：
    索引里留着一条指向不存在文件的指针，读起来像「这条记忆在」，而它不在。

    删除和写入同一道闸：team 读得到就删得动，private 只认本人（`_writable`）。
    """
    try:
        project_id = uuid.UUID(str(body.get("project_id") or ""))
    except ValueError as exc:
        raise ValidationError("project_id 无效") from exc
    which = _scope_of(str(body.get("scope") or "team"))
    owner = _owner_of(which, (body.get("owner_handle") or "").strip() or None)
    actor = await _writable(db, resolver, project_id, owner)
    raw_path = str(body.get("path") or "").strip()
    if not raw_path:
        raise ValidationError("path 不能为空")
    path = _checked_path(raw_path)
    if path == INDEX_NAME:
        raise ValidationError(
            "索引本身不删——它不是一个作用域里的第一条记忆；要清空就把它写成空文件"
        )
    raw_version = body.get("version")
    expected: int | None = None
    if raw_version is not None:
        try:
            expected = int(raw_version)
        except (TypeError, ValueError) as exc:
            raise ValidationError("version 必须是整数") from exc
    try:
        await MemoryFileStore(db).delete(
            project_id=project_id,
            scope=which,
            owner_handle=owner,
            path=path,
            expected_version=expected,
        )
    except MemoryFileMissing as exc:
        raise ValidationError(str(exc)) from exc
    return ok({"deleted": path, "deleted_by": actor})
