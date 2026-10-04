"""记忆文件的真相层：读写 `memory_files`，并把 L1 索引拼成注入用的那一段。

数据库是真相，会话目录里那一份是副本（见 `files.py` 与 `session.py`）。所以这里
只有两件事：按 (项目, 作用域, 路径) 存取，以及「这一轮的索引长什么样」。

**冲突是拒绝，不是合并。** 每一行带 `version`，写入必须带回来读到的那一版；对
不上就什么都不做，把当前的那一版一起还给调用方，让它重读再改。合并两版文本要
先看懂两边改了什么，而 `MEMORY.md` 和记忆文件都是散文——自动合并散文的唯一结果
是两句互相矛盾的话安静地并排躺在一起。拒绝一次写入是便宜的：agent 就在现场，
重读一次就行。
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.memory.files import (
    INDEX_NAME,
    MemoryFileScope,
    check_path,
    fit_index,
    limit_breach,
    prefix_of,
)
from app.domain.memory.models import MemoryFileRecord


class MemoryFileConflict(ConflictError):
    """这一版已经不是读到的那一版了。

    带着两个版本号，是因为调用方（agent 或人）要做的下一件事是重读；只说
    「冲突了」等于让它再猜一次自己读到的是什么。
    """

    def __init__(self, path: str, expected: int | None, current: int | None):
        self.path = path
        self.expected = expected
        self.current = current
        if current is None:
            detail = say("memoryFileDeleted")
        elif expected is None:
            detail = say("memoryFileAlreadyExists", current=current)
        else:
            detail = say("memoryFileVersionMoved", expected=expected, current=current)
        super().__init__(
            say("memoryFileVersionConflict", path=path, detail=detail),
            # 两个版本号也进 `data`：那句话是给人读的，而重读这件事要动手，动手
            # 的人（界面上的那次重试）需要知道自己在重读第几版。
            data={"path": path, "expected": expected, "current": current},
        )


class MemoryFileLimit(ValidationError):
    """这一版超了单条上限（`files.limit_breach`），一个字都没写。"""

    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(say("memoryFileNotWritten", path=path, reason=reason))


class MemoryFileMissing(NotFoundError):
    """要改的这个文件不在。"""


@dataclass(frozen=True)
class IndexSection:
    """一份索引，和它是谁的。"""

    label: str
    prefix: str
    text: str


@dataclass(frozen=True)
class MemoryIndex:
    """这一轮注入的 L1 索引，和它被截断时要说的话。"""

    sections: list[IndexSection]
    warnings: list[str]

    def is_empty(self) -> bool:
        return not any(section.text.strip() for section in self.sections)


def _owner(scope: MemoryFileScope, handle: str | None) -> str:
    return "" if scope is MemoryFileScope.team else (handle or "")


class MemoryFileStore:
    """`memory_files` 的读写。一个请求一个实例，跟着那一个会话走。"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def list(
        self,
        project_id: uuid.UUID,
        scope: MemoryFileScope,
        owner_handle: str | None = None,
        *,
        for_update: bool = False,
    ) -> list[MemoryFileRecord]:
        """一个作用域下的全部文件，路径序（索引在最前，其余按名字）。"""
        stmt = (
            select(MemoryFileRecord)
            .where(
                MemoryFileRecord.project_id == project_id,
                MemoryFileRecord.scope == scope,
                MemoryFileRecord.owner_handle == _owner(scope, owner_handle),
            )
            .order_by(MemoryFileRecord.path)
        )
        if for_update:
            stmt = stmt.with_for_update()
        return list((await self._session.scalars(stmt)).all())

    async def get(
        self,
        project_id: uuid.UUID,
        scope: MemoryFileScope,
        owner_handle: str | None,
        path: str,
        *,
        for_update: bool = False,
    ) -> MemoryFileRecord | None:
        check_path(path)
        stmt = select(MemoryFileRecord).where(
            MemoryFileRecord.project_id == project_id,
            MemoryFileRecord.scope == scope,
            MemoryFileRecord.owner_handle == _owner(scope, owner_handle),
            MemoryFileRecord.path == path,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return await self._session.scalar(stmt)

    async def write(
        self,
        *,
        project_id: uuid.UUID,
        scope: MemoryFileScope,
        owner_handle: str | None,
        path: str,
        content: str,
        updated_by: str,
        expected_version: int | None,
    ) -> MemoryFileRecord:
        """写入一版；版本对不上就抛 :class:`MemoryFileConflict`，超了单条上限就抛
        :class:`MemoryFileLimit`。

        ``expected_version=None`` 是**新建**：已经有一条同名记忆时它也是冲突，
        因为「先查重，再新建」是写记忆的规矩，而一个新的空文件正好是它最容易被
        绕过的地方。要覆盖已经存在的那一条，得先读到它的版本号。

        新建这一步是**先查后插**，两个同时起手的新建会在 ``get`` 之后、``flush``
        之前撞上唯一约束——上面那条「已经有一条了」正是这个竞赛的结果，所以输家
        的 ``IntegrityError`` 不是故障，是答案，接住它重读一次并发同样的冲突。

        接住它要有保存点，而保存点有三件事必须按顺序做对，少一件会话就整条作废
        （那一次 ``flush`` 失败会把「回滚过了」这个状态一路标到外层事务上，接着
        这个请求里要发的那条 409 自己就先炸了）：对象在保存点**里面** add
        （``begin_nested()`` 会先把待写对象 flush 出去，在外面 add 的那一行 INSERT
        落在保存点外面，回滚不掉）、撞上时**自己动手**回滚这一次保存点（让异常从
        ``async with`` 里传出去，等于把失败标记传给外层）、回滚之后再读一次。
        """
        check_path(path)
        row = await self.get(project_id, scope, owner_handle, path, for_update=True)
        if breach := limit_breach(path, content, row.content if row else None):
            raise MemoryFileLimit(path, breach)
        if row is None:
            if expected_version is not None:
                raise MemoryFileConflict(path, expected_version, None)
            row = MemoryFileRecord(
                project_id=project_id,
                scope=scope,
                owner_handle=_owner(scope, owner_handle),
                path=path,
                content=content,
                version=1,
                updated_by=updated_by,
            )
            savepoint = await self._session.begin_nested()
            try:
                self._session.add(row)
                await self._session.flush()
            except IntegrityError:
                await savepoint.rollback()
                current = await self.get(project_id, scope, owner_handle, path)
                if current is None:
                    # 不是这个竞赛——撞的是我们不认识的约束。宁可 500，也不要把
                    # 一次没写进去的写入说成「写好了」。
                    raise
                # `from None`：这次冲突是规矩本身的结果，而那一条 IntegrityError
                # 是它的实现细节——挂在链上，读日志的人会以为出了事故。
                raise MemoryFileConflict(
                    path, expected_version, current.version
                ) from None
            await savepoint.commit()
            return row
        if expected_version is None or expected_version != row.version:
            raise MemoryFileConflict(path, expected_version, row.version)
        row.content = content
        row.version = row.version + 1
        row.updated_by = updated_by
        await self._session.flush()
        return row

    async def delete(
        self,
        *,
        project_id: uuid.UUID,
        scope: MemoryFileScope,
        owner_handle: str | None,
        path: str,
        expected_version: int | None,
    ) -> None:
        """删掉一条记忆；索引那一行由调用方一并删（它才看得见两份）。"""
        check_path(path)
        row = await self.get(project_id, scope, owner_handle, path, for_update=True)
        if row is None:
            raise MemoryFileMissing(say("memoryFileGone", path=path))
        if expected_version is not None and expected_version != row.version:
            raise MemoryFileConflict(path, expected_version, row.version)
        await self._session.execute(
            delete(MemoryFileRecord).where(MemoryFileRecord.id == row.id)
        )
        await self._session.flush()

    async def index_text(
        self,
        project_id: uuid.UUID,
        scope: MemoryFileScope,
        owner_handle: str | None = None,
    ) -> str | None:
        """这个作用域的索引正文；一个字的记忆都没有时 None。"""
        row = await self.get(project_id, scope, owner_handle, INDEX_NAME)
        if row is None or not row.content.strip():
            return None
        return row.content


async def private_owners(session: AsyncSession, project_id: uuid.UUID) -> list[str]:
    """这个项目里有哪些人的 private 树，人名序。

    一轮会话只铺在场的几个人的 private（`memory_index` 的预算那一段），而整理要
    看的是**这个项目全部**的私人记忆：它是唯一一个把整棵树放在一起看的时刻，漏掉
    一个没说过话的人，等于他的记忆没有人整理。所以这里不问「谁在场」，问「谁有」。
    """
    rows = await session.scalars(
        select(MemoryFileRecord.owner_handle)
        .where(
            MemoryFileRecord.project_id == project_id,
            MemoryFileRecord.scope == MemoryFileScope.private,
        )
        .distinct()
        .order_by(MemoryFileRecord.owner_handle)
    )
    return [handle for handle in rows.all() if handle]


async def memory_index(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    speaker_handles: list[str],
) -> MemoryIndex:
    """这一轮注入的 L1 索引：team 一份，本轮发言人一人一份。

    **只读在场的这几个人。** 项目里的人可以很多，而每一轮要用的只是同席这几位
    ——这不是权限（private 记忆只有本人和管理员看得见，另有一道闸），是预算：
    索引是每一轮都要付的，付在一个没说话的人的偏好上是白付。

    超预算照样注入（截断后），并在注入块里说清截断过：静默截断读起来就是「索引
    就这么长」，于是没有人会去修它。
    """
    store = MemoryFileStore(session)
    sections: list[IndexSection] = []
    warnings: list[str] = []
    wanted: list[tuple[MemoryFileScope, str | None, str]] = [
        (MemoryFileScope.team, None, "项目共享（team）")
    ]
    for handle in dict.fromkeys(speaker_handles):
        if handle:
            wanted.append(
                (MemoryFileScope.private, handle, f"关于 {handle}（private）")
            )
    for scope, owner, label in wanted:
        text = await store.index_text(project_id, scope, owner)
        if text is None:
            continue
        fitted, warning = fit_index(text)
        if warning:
            warnings.append(f"{prefix_of(scope, owner)}/{INDEX_NAME}：{warning}")
        sections.append(
            IndexSection(label=label, prefix=prefix_of(scope, owner), text=fitted)
        )
    return MemoryIndex(sections=sections, warnings=warnings)
