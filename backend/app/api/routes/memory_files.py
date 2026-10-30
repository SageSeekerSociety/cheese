"""记忆文件的读、写、删 —— 给界面和 agent 用的那一面。

真相在 `memory_files`（见 `app/domain/memory/files_store.py`），这里只是把它露
出来。两条规矩，都是产品决定，不是实现细节：

- **team 是项目里读得到它的人共读的，改它要有配置 AI 队友的权限**（#3830）。
  agent 一轮对账改了 team，会在写它的那段对话的现场里留一条带 diff 的记录，所以
  「谁删的」追得回来。
- **private 只有本人看得见、写得动，项目管理员也不例外。** 它多半是芝士从这个人
  的私聊里记下的，私聊管理员进不去，记忆也不该是后门；「这是谁的判断」正是
  private 这一层唯一要保住的东西。越权读写一律 403。

冲突这一路（版本对不上）在 `MemoryFileStore` 里是 409，带着当前版本号回来；这里
不吞它、也不重试——写入方就在现场，重读一次比自动合并两段散文安全。
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, page, typed_response
from app.api.write_access import ROUTE_DECIDES, Can, InProject
from app.core.errors import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.memory import history
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
from app.domain.memory.writers import MemoryWriter
from app.domain.project_permissions import Permission

router = APIRouter(prefix="/memory/files", tags=["memory"])


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
    """A project's scope. A person's own memory is in no project: it is read
    and written on their settings page and by their own 芝士, never here."""
    try:
        scope = MemoryFileScope(raw)
    except ValueError as exc:
        raise ValidationError(say("memoryScopeInvalid")) from exc
    if scope is MemoryFileScope.personal:
        raise ValidationError(say("memoryScopeInvalid"))
    return scope


async def _readable(
    db: AsyncSession,
    resolver: ActorResolverDep,
    project_id: uuid.UUID,
    owner: str | None,
) -> str:
    """谁在读，读不读得到，一起答完；返回读的人自己的 handle。

    team：项目里读得到它的人就读得到。private：只有本人，项目管理员也不例外——
    它多半是芝士从这个人的私聊里记下的，私聊管理员进不去，记忆也不该是后门。
    别人问起一个 private 作用域时答 403 而不是 404——private 的存在本身不是
    秘密，里面的内容才是，而 404 会让本人也以为自己的记忆不见了。
    """
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    if owner is not None and owner != actor.handle:
        raise ForbiddenError(say("privateMemoryOwnerSees"))
    return actor.handle


async def _writable(
    db: AsyncSession,
    resolver: ActorResolverDep,
    project_id: uuid.UUID,
    owner: str | None,
) -> str:
    """谁在写/删，写不写得动，一起答完；返回动手的人自己的 handle。

    private 只认本人，和读的那一道同一条规则、同一个码。team 是项目的 AI 队友
    共用的那一份，改它就是配置 AI 队友（#3830）：读得到的人不一定改得动。
    """
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(
        actor,
        project_id=project_id,
        permission=Permission.configure_agents if owner is None else None,
    )
    if owner is not None and owner != actor.handle:
        raise ForbiddenError(say("privateMemoryOwnerSees"))
    return actor.handle


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
        raise ValidationError(say("privateMemoryNeedsOwner"))
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


@router.put("", dependencies=[ROUTE_DECIDES])
async def write_memory_file(
    body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """写入一版。`version` 缺省 = 新建（重名就是冲突，见 `MemoryFileStore.write`）。

    格式不对**不拒绝**，只回一条警告：要是把格式变成闸，第一条记忆就得同时写对
    frontmatter、索引行和路径——三样里错一样就一个字都存不下。**超了单条上限拒绝**
    （422，`MemoryFileStore.write`）：写的人手上就有这一条，当场就改得短。

    谁写得动由 `_writable` 答：team 要配置 AI 队友的权限，private 只认本人。
    """
    try:
        project_id = uuid.UUID(str(body.get("project_id") or ""))
    except ValueError as exc:
        raise ValidationError(say("projectIdInvalid")) from exc
    which = _scope_of(str(body.get("scope") or "team"))
    owner = _owner_of(which, (body.get("owner_handle") or "").strip() or None)
    actor = await _writable(db, resolver, project_id, owner)
    path = _checked_path(str(body.get("path") or ""))
    content = body.get("content")
    if not isinstance(content, str):
        raise ValidationError(say("contentMustBeString"))
    raw_version = body.get("version")
    expected: int | None = None
    if raw_version is not None:
        try:
            expected = int(raw_version)
        except (TypeError, ValueError) as exc:
            raise ValidationError(say("versionMustBeInteger")) from exc
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
            writer=MemoryWriter.panel,
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
            writer=MemoryWriter.panel,
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
        writer=MemoryWriter.panel,
    )
    warning = None
    if memory.description and len(memory.description) > 150:
        warning = "description 超过 150 字符：它只用来判相关性，长的那部分放正文里。"
    return ok({"file": _file_out(row), "warning": warning})


@router.post("/delete", dependencies=[ROUTE_DECIDES])
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
        raise ValidationError(say("projectIdInvalid")) from exc
    which = _scope_of(str(body.get("scope") or "team"))
    owner = _owner_of(which, (body.get("owner_handle") or "").strip() or None)
    actor = await _writable(db, resolver, project_id, owner)
    raw_path = str(body.get("path") or "").strip()
    if not raw_path:
        raise ValidationError(say("pathEmpty"))
    path = _checked_path(raw_path)
    if path == INDEX_NAME:
        raise ValidationError(say("memoryIndexNotDeleted"))
    raw_version = body.get("version")
    expected: int | None = None
    if raw_version is not None:
        try:
            expected = int(raw_version)
        except (TypeError, ValueError) as exc:
            raise ValidationError(say("versionMustBeInteger")) from exc
    try:
        await MemoryFileStore(db).delete(
            project_id=project_id,
            scope=which,
            owner_handle=owner,
            path=path,
            expected_version=expected,
            updated_by=actor,
            writer=MemoryWriter.panel,
        )
    except MemoryFileMissing as exc:
        raise ValidationError(str(exc)) from exc
    return ok({"deleted": path, "deleted_by": actor})


class MemoryChangeOut(BaseModel):
    """一次改动：谁、从哪条路、什么时候、哪一条，和它的 diff。"""

    id: str
    scope: str
    owner_handle: str
    path: str
    writer: str
    changed_by: str
    created_at: str
    created: bool
    deleted: bool
    diff: str


class MemoryChangesOut(BaseModel):
    """一页改动，新的在前；``next`` 是下一页的 ``before``，读到头了为空。"""

    data: list[MemoryChangeOut]
    next: str | None = None


class MemoryFileOut(BaseModel):
    id: str
    scope: str
    owner_handle: str
    path: str
    content: str
    version: int
    updated_by: str
    created_at: str
    updated_at: str


class MemoryRestoredOut(BaseModel):
    """恢复之后那条记忆现在的样子。"""

    file: MemoryFileOut


def _change_out(row: history.Change) -> dict:
    return {
        "id": str(row.id),
        "scope": row.scope.value,
        "owner_handle": row.owner_handle,
        "path": row.path,
        "writer": row.writer.value,
        "changed_by": row.changed_by,
        "created_at": row.created_at.isoformat(),
        # 是新建还是删除，面板据此说这一行做了什么、恢复按钮在不在（新建没有
        # 「改之前」可恢复）。正文不整份给：一个改动很密的索引一个月能有几百版，
        # 面板要画的只是差在哪。
        "created": row.before is None,
        "deleted": row.after is None,
        "diff": history.diff_of(row),
    }


def _change_cursor(row: history.Change) -> str:
    return f"{row.created_at.isoformat()}|{row.id}"


def _read_change_cursor(cursor: str) -> history.Cursor:
    try:
        at, _, of = cursor.partition("|")
        return datetime.fromisoformat(at), uuid.UUID(of)
    except ValueError as error:
        raise BadRequestError(say("listCursorUnreadable")) from error


@router.get("/history", **typed_response(MemoryChangesOut))
async def list_memory_changes(
    db: DbSession,
    resolver: ActorResolverDep,
    project_id: uuid.UUID,
    scope: str = Query("team"),
    owner_handle: str | None = None,
    before: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = history.PAGE,
) -> dict:
    """一个作用域的改动（`history.RETENTION` 之内，新的在前），一页一页地读：
    ``before`` 是上一页的 ``next``，``next`` 为空就是读到头了。保留期内的每一条
    都翻得到。

    谁读得到和读记忆文件同一道门（`_readable`）：team 是项目里的人，某个人的
    private 只有他本人，管理员也一样答 403。
    """
    which = _scope_of(scope)
    owner = _owner_of(which, owner_handle)
    await _readable(db, resolver, project_id, owner)
    rows, more = await history.changes(
        db,
        project_id,
        which,
        owner,
        before=None if before is None else _read_change_cursor(before),
        limit=limit,
    )
    return ok(
        {
            "data": [_change_out(r) for r in rows],
            "next": _change_cursor(rows[-1]) if more and rows else None,
        }
    )


@router.post(
    "/projects/{project_id}/history/{change_id}/restore",
    **typed_response(MemoryRestoredOut),
)
async def restore_memory_change(
    change_id: uuid.UUID,
    at: Annotated[InProject, Can(Permission.ask_agent)],
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """把一条记忆写回这一次改动之前的样子（`history.restore`）。

    `Can(ask_agent)` 是进门的那一道：能和芝士说话的人才有记忆可恢复。进门之后
    和写入同一道门（`_writable`）：恢复就是一次写入，team 要能配置 AI 队友，
    private 只有本人，写不动这个作用域的人也恢复不了它。恢复本身留下一条新的
    改动记录。
    """
    row = await history.change(db, change_id)
    if row is None or row.project_id != at.project.id:
        raise NotFoundError(say("memoryChangeNotFound"))
    owner = row.owner_handle or None
    actor = await _writable(db, resolver, row.project_id, owner)
    written = await history.restore(db, row, by=actor)
    return ok({"file": _file_out(written)})
