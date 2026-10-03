"""会话目录里那一份记忆：铺下去，收回来（后端这一半）。

agent 用自己手里的 Write/Edit 改会话目录里的 `.md`，平台这一份在同一时间也可能
被别的会话或界面改。合成哪一份的规矩在 `tree.py`，跑在会话机上的是同一段代码；
这里只做前端这一半的两件事：

- **铺下去**：把数据库这一份读成请求（`read_tree`）；
- **收回来**：把对账结果写回数据库，并把改动讲成一句话（`apply_tree`）。

**删除只在自己点过名的作用域里认。** 会话机上那棵树里，这一轮没轮到的 private
（别人的偏好）会被收走，收走之后它当然不在回来的那份树里；把「不在回来的树里」
一律读成「被删了」，就是把一个没说话的人的偏好当成 agent 删掉的东西从数据库里抹
掉。点名过的作用域才是这一次对账的范围，范围外的一律不碰。

**正文没变就不写。** 每一次写入都会推高 `version`，而版本号是冲突判据——每轮对一
次账就把所有行都推高一版，等于把这个判据作废。所以先比正文，一样就什么都不做。
"""

import uuid
from dataclasses import dataclass, field
from difflib import unified_diff

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.memory.files import MemoryFileScope, scoped_prefix
from app.domain.memory.files_store import (
    MemoryFileConflict,
    MemoryFileLimit,
    MemoryFileStore,
)

#: diff 里一条路径最多留几行。事件是给人扫一眼的，不是补丁文件；一整份被重写的
#: 索引能有几千行，全塞进去就是把房间里那条事件变成一个没人展开的附件。
DIFF_MAX_LINES = 200


@dataclass(frozen=True)
class StoredTree:
    """数据库这一份，按对账要的三个形状各存一份。"""

    #: 请求用的那一份（作用域前缀 → 文件名 → 正文）。
    scopes: dict[str, dict[str, str]]
    #: 路径 → 正文，用来算 diff 和判「变了没有」。
    contents: dict[str, str]
    #: 路径 → 版本号，回写时对号入座。
    versions: dict[str, int]


@dataclass(frozen=True)
class MemoryChange:
    """这一次对账真的改了什么。空的就是什么都没变——那就不说话。"""

    added: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    #: 对账这几秒里又被人改过、这一次没写进去的（下一次对账自然会重来）。
    conflicted: tuple[str, ...] = ()
    #: 会话那一版被平台盖掉的（路径 → 它的正文，删除是空串）。
    refused: dict[str, str] = field(default_factory=dict)
    #: 会话写的、超了单条上限没收的（路径 → 原因）。
    rejected: dict[str, str] = field(default_factory=dict)
    #: 路径 → 那一条的统一 diff。按条存而不是拼成一份：说给谁听是按作用域分的
    #: （team 的说进项目总览，某个人的 private 只说进他的私聊），而一件事说给谁
    #: 听决定了哪几行能跟着一起出去。
    diffs: dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (self.added or self.updated or self.removed or self.conflicted)

    def scoped(self, prefix: str) -> "MemoryChange":
        """只留 ``prefix`` 这一棵树里的改动（team / private/<handle>）。"""

        def within(paths: tuple[str, ...]) -> tuple[str, ...]:
            return tuple(p for p in paths if p.startswith(f"{prefix}/"))

        def keyed(mapping: dict[str, str]) -> dict[str, str]:
            return {
                p: value for p, value in mapping.items() if p.startswith(f"{prefix}/")
            }

        return MemoryChange(
            added=within(self.added),
            updated=within(self.updated),
            removed=within(self.removed),
            conflicted=within(self.conflicted),
            refused=keyed(self.refused),
            rejected=keyed(self.rejected),
            diffs=keyed(self.diffs),
        )

    @property
    def diff(self) -> str:
        """这一份改动合起来的统一 diff，给房间里那条事件用。"""
        return _clamp("".join(self.diffs[path] for path in sorted(self.diffs)))

    def summary(self) -> str:
        """房间里那一行说的那句话；什么都没改是空串。"""
        parts = []
        if self.added:
            parts.append(say("memoryAdded", count=len(self.added)))
        if self.updated:
            parts.append(say("memoryUpdated", count=len(self.updated)))
        if self.removed:
            parts.append(say("memoryRemoved", count=len(self.removed)))
        if self.conflicted:
            parts.append(say("memoryConflicted", count=len(self.conflicted)))
        if not parts:
            return ""
        said = parts[0]
        for part in parts[1:]:
            said = say("listJoin", first=said, second=part)
        return said


def _split(path: str) -> tuple[str, str]:
    parts = path.split("/")
    if parts[0] == "team":
        return "team", parts[1]
    return f"private/{parts[1]}", parts[2]


def prefix_of_scope(scope: MemoryFileScope, owner: str | None) -> str:
    return scoped_prefix(scope, owner)


async def read_tree(
    session: AsyncSession,
    project_id: uuid.UUID,
    scopes: list[tuple[MemoryFileScope, str | None]],
) -> StoredTree:
    """点名的那几个作用域，数据库现在是什么样。"""
    store = MemoryFileStore(session)
    tree: dict[str, dict[str, str]] = {}
    contents: dict[str, str] = {}
    versions: dict[str, int] = {}
    for scope, owner in scopes:
        prefix = prefix_of_scope(scope, owner)
        files: dict[str, str] = {}
        for row in await store.list(project_id, scope, owner):
            path = f"{prefix}/{row.path}"
            files[row.path] = row.content
            contents[path] = row.content
            versions[path] = row.version
        tree[prefix] = files
    return StoredTree(scopes=tree, contents=contents, versions=versions)


async def apply_tree(
    session: AsyncSession,
    project_id: uuid.UUID,
    stored: StoredTree,
    response: dict,
    *,
    scopes: list[tuple[MemoryFileScope, str | None]],
    updated_by: str,
) -> MemoryChange:
    """把会话机回来的那一份写进数据库，并说清改了什么。

    回来的 `files` 是**整个受管树**（不只是改过的），所以这里逐条比正文：一样就
    跳过，不一样才写，没见过的路径就是新建，点过名的作用域里少了的路径就是删除。
    """
    store = MemoryFileStore(session)
    wanted = {prefix_of_scope(scope, owner): (scope, owner) for scope, owner in scopes}
    files = response.get("files") or {}
    refused = {
        str(path): str(content)
        for path, content in (response.get("refused") or {}).items()
    }
    rejected = {
        str(path): str(reason)
        for path, reason in (response.get("rejected") or {}).items()
    }
    added: list[str] = []
    updated: list[str] = []
    conflict: list[str] = []
    before: dict[str, str] = {}
    for path, content in files.items():
        if not isinstance(content, str):
            continue
        try:
            prefix, name = _split(path)
        except (IndexError, KeyError):
            continue
        if prefix not in wanted:
            # 这一次没点它的名：它只是没在这一轮的树里，不是被删了。
            continue
        scope, owner = wanted[prefix]
        stored_content = stored.contents.get(path)
        if stored_content == content:
            continue
        before[path] = stored_content or ""
        try:
            await store.write(
                project_id=project_id,
                scope=scope,
                owner_handle=owner,
                path=name,
                content=content,
                updated_by=updated_by,
                expected_version=stored.versions.get(path),
            )
        except MemoryFileConflict:
            # 对账的这几秒里又有人改了这一条。它不重试：下一次对账就是一次重
            # 试，而这一次的内容会被下一次铺下去的那一版盖回来。
            conflict.append(path)
            continue
        except MemoryFileLimit as exc:
            # 会话那一侧本该先拦下它（`tree.sync_tree`）；拦不下的是一个比这一
            # 版旧的会话，这里是最后一道。
            before.pop(path)
            rejected[path] = exc.reason
            continue
        (added if stored_content is None else updated).append(path)
    removed: list[str] = []
    for path in stored.contents:
        if path in files:
            continue
        try:
            prefix, name = _split(path)
        except (IndexError, KeyError):
            continue
        if prefix not in wanted:
            continue
        scope, owner = wanted[prefix]
        before[path] = stored.contents[path]
        try:
            await store.delete(
                project_id=project_id,
                scope=scope,
                owner_handle=owner,
                path=name,
                expected_version=stored.versions.get(path),
            )
        except (MemoryFileConflict, NotFoundError):
            conflict.append(path)
            continue
        removed.append(path)
    return MemoryChange(
        added=tuple(sorted(added)),
        updated=tuple(sorted(updated)),
        removed=tuple(sorted(removed)),
        conflicted=tuple(sorted(set(conflict))),
        refused=refused,
        rejected=rejected,
        diffs=_diffs(before, files),
    )


def _diffs(before: dict[str, str], files: dict[str, str]) -> dict[str, str]:
    """改动的统一 diff，一条记忆一段，给房间里那条事件用。

    一段一段地算：一条记忆一个文件，改动落在哪一条上比「整棵树差在哪」有用——
    人扫一眼要知道的是哪条记忆变了，不是文件树变了几行。分开存还让「这一件事
    说给谁听」有得选：某个人的 private 那一段不能跟着 team 的那一段一起发出去。
    """
    out: dict[str, str] = {}
    for path in sorted(before):
        old = before[path].splitlines(keepends=True)
        new = files.get(path, "").splitlines(keepends=True)
        if old == new:
            continue
        lines = [f"--- {path}\n+++ {path}\n"]
        lines.extend(unified_diff(old, new, fromfile="", tofile="", n=1, lineterm="\n"))
        out[path] = "".join(lines)
    return out


def _clamp(text: str) -> str:
    lines = text.splitlines()
    if len(lines) <= DIFF_MAX_LINES:
        return text
    kept = "\n".join(lines[:DIFF_MAX_LINES])
    return f"{kept}\n…（还有 {len(lines) - DIFF_MAX_LINES} 行，略）\n"
