"""会话目录里那一份记忆：铺下去，收回来（后端这一半）。

agent 用自己手里的 Write/Edit 改会话目录里的 `.md`，平台这一份在同一时间也可能
被别的会话或界面改。合成哪一份的规矩在 `tree.py`，跑在会话机上的是同一段代码；
这里只做前端这一半的两件事：

- **铺下去**：把数据库这一份读成请求（`read_tree`）；
- **收回来**：把对账结果写回数据库，并把改动讲成一句话（`apply_tree`）。

**删除只在自己点过名的作用域里认。** 会话机上那棵树里，这一轮没轮到的 private
（别人的偏好）不在回来的那份树里；把「不在回来的树里」一律读成「被删了」，就是把
一个没说话的人的偏好当成 agent 删掉的东西从数据库里抹掉。点名过的作用域才是这一
次对账的范围。

**没点名、会话却交回来的 private，替它的主人收。** 一个座位的树一次只放一个人
的 private（`runner.memory_root`）；这个座位换了一位说话的人，上一位的 private
这一次没点名，会话把它连同基线原样交回来、从树上拿走（`tree.withdrawn_prefixes`）。它里面可能有那一位的会话还没对过账的改动，所以这
里拿数据库现在那一份跟它按同一条三方规矩合一次，合出来的就是那个人的
（`apply_withdrawn`）。

**多人地方只铺、只收 communication。** 频道主线、任务、支线里，一个人的 private
铺下去的是 `files.talk_view` 那一份（``views``）：读的时候按类型挑，收的时候只收新
记的那一类（`files.policy_breach`），索引只收指向它们的新行，合回他真正的那份索引
（`files.merge_view_index`）——那一份里别的条目从没铺下去，所以「不在回来的树里」
在这里不是删除。

**收不下的写入要说出来。** 平台不收的（多人地方里别的类型、私聊和私密频道里的
team、会话机报回来的放错地方的文件）进 `MemoryChange.dropped`，下一轮告诉写它的
agent（`platform_notices.memory_dropped_notice`）。

**正文没变就不写。** 每一次写入都会推高 `version`，而版本号是冲突判据——每轮对一
次账就把所有行都推高一版，等于把这个判据作废。所以先比正文，一样就什么都不做。
"""

import uuid
from dataclasses import dataclass, field, replace
from difflib import unified_diff

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.memory.files import (
    INDEX_NAME,
    PRIVATE_PREFIX,
    MemoryFileError,
    MemoryFileScope,
    check_path,
    merge_view_index,
    parse_index,
    policy_breach,
    scoped_prefix,
    talk_view,
)
from app.domain.memory.files_store import (
    MemoryFileConflict,
    MemoryFileLimit,
    MemoryFileStore,
)
from app.domain.memory.tree import sync_tree
from app.domain.memory.writers import MemoryWriter

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
    #: 多人地方铺下的那几棵（`files.talk_view`）的前缀。
    views: frozenset[str] = frozenset()
    #: 那几棵里索引的路径 → 他真正的那份索引（没有就是空串）。`contents` 里是铺下
    #: 去的那一份，`versions` 里是真正那一份的版本号。
    real_index: dict[str, str] = field(default_factory=dict)


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
    #: 平台没有收、也没有留副本的写入（路径 → 原因）：多人地方里别的类型、只读的
    #: team、写错地方的文件。
    dropped: dict[str, str] = field(default_factory=dict)
    #: `rejected` 里的新文件中，会话这一轮加进索引的那一行也没收的（路径）。比这
    #: 一版旧的会话不报它，那就是空的：没说拿掉过，就不叫 agent 加回去。
    unindexed: tuple[str, ...] = ()
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
            dropped=keyed(self.dropped),
            unindexed=within(self.unindexed),
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
    *,
    views: frozenset[str] = frozenset(),
) -> StoredTree:
    """点名的那几个作用域，数据库现在是什么样。``views`` 里的 private 读成多人
    地方铺的那一份（`files.talk_view`）。"""
    store = MemoryFileStore(session)
    tree: dict[str, dict[str, str]] = {}
    contents: dict[str, str] = {}
    versions: dict[str, int] = {}
    real_index: dict[str, str] = {}
    laid_views: set[str] = set()
    for scope, owner in scopes:
        prefix = prefix_of_scope(scope, owner)
        rows = await store.list(project_id, scope, owner)
        files = {row.path: row.content for row in rows}
        versions.update({f"{prefix}/{row.path}": row.version for row in rows})
        if scope is MemoryFileScope.private and prefix in views:
            laid_views.add(prefix)
            real_index[f"{prefix}/{INDEX_NAME}"] = files.get(INDEX_NAME, "")
            files = talk_view(files)
            versions = {
                path: version
                for path, version in versions.items()
                if not path.startswith(f"{prefix}/")
                or path[len(prefix) + 1 :] in files
                or path == f"{prefix}/{INDEX_NAME}"
            }
        contents.update({f"{prefix}/{name}": body for name, body in files.items()})
        tree[prefix] = files
    return StoredTree(
        scopes=tree,
        contents=contents,
        versions=versions,
        views=frozenset(laid_views),
        real_index=real_index,
    )


async def talk_owners(
    session: AsyncSession, project_id: uuid.UUID, handles: list[str]
) -> list[str]:
    """``handles`` 里有 communication 记录的那几位，原序：多人地方里，只对他们说
    「先读他的记录」（`prompts.talk_pointer`）。"""
    store = MemoryFileStore(session)
    owners = []
    for handle in handles:
        rows = await store.list(project_id, MemoryFileScope.private, handle)
        if any(
            name != INDEX_NAME for name in talk_view({r.path: r.content for r in rows})
        ):
            owners.append(handle)
    return owners


#: 私聊和私密频道里写进 team 的改动不收（#3830）。
_TEAM_READ_ONLY = (
    "私聊和私密频道里 team/ 只读：在这里听到的话只有在场的人能读，"
    "写进全项目共看的 team 就成了别人的后门"
)


async def apply_tree(
    session: AsyncSession,
    project_id: uuid.UUID,
    stored: StoredTree,
    response: dict,
    *,
    scopes: list[tuple[MemoryFileScope, str | None]],
    updated_by: str,
    writer: MemoryWriter,
    read_only: frozenset[str] = frozenset(),
    people: dict[str, str] | None = None,
) -> MemoryChange:
    """把会话机回来的那一份写进数据库，并说清改了什么。

    回来的 `files` 是**整个受管树**（不只是改过的），所以这里逐条比正文：一样就
    跳过，不一样才写，没见过的路径就是新建，点过名的作用域里少了的路径就是删除。

    ``read_only`` 里的作用域铺下去了、但不收（私聊和私密频道里的 team）：那里的
    改动进 `dropped`。``stored.views`` 里的那几棵按多人地方的规矩收（模块开头）。
    这里是会话机那一侧（`tree.sync_tree`）之后的最后一道：比这一版旧的会话机
    不按这几条拦。

    ``writer`` 是这一份从哪来的（一轮对话、整理的哪一轮），每一条改动都按它记进
    改动记录（`MemoryFileStore._record`）。
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
    dropped = {
        str(path): str(reason)
        for path, reason in (response.get("dropped") or {}).items()
    }
    unindexed = tuple(sorted(str(path) for path in response.get("unindexed") or ()))
    added: list[str] = []
    updated: list[str] = []
    conflict: list[str] = []
    before: dict[str, str] = {}
    written: dict[str, str] = {}

    async def put(path: str, scope, owner, name: str, content: str, old) -> None:
        try:
            await store.write(
                project_id=project_id,
                scope=scope,
                owner_handle=owner,
                path=name,
                content=content,
                updated_by=updated_by,
                expected_version=stored.versions.get(path),
                writer=writer,
            )
        except MemoryFileConflict:
            # 对账的这几秒里又有人改了这一条。它不重试：下一次对账就是一次重
            # 试，而这一次的内容会被下一次铺下去的那一版盖回来。
            conflict.append(path)
            return
        except MemoryFileLimit as exc:
            # 会话那一侧本该先拦下它（`tree.sync_tree`）；拦不下的是一个比这一
            # 版旧的会话，这里是最后一道。
            rejected[path] = exc.reason
            return
        before[path] = old or ""
        written[path] = content
        (added if old is None else updated).append(path)

    # 记录先收，索引后收：索引新加的行要看它指的那一条这一次收没收下。
    ordered = sorted(
        (
            (path, content)
            for path, content in files.items()
            if isinstance(content, str)
        ),
        key=lambda item: item[0].rsplit("/", 1)[-1] == INDEX_NAME,
    )
    for path, content in ordered:
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
        if prefix in read_only:
            dropped[path] = _TEAM_READ_ONLY
            continue
        if name == INDEX_NAME and prefix in stored.views:
            real = stored.real_index.get(path, "")
            merged, refused_lines = merge_view_index(
                stored_content or "",
                content,
                real,
                _kept_talk(prefix, stored, files) - _names(prefix, dropped),
            )
            if refused_lines:
                dropped[path] = _view_index_reason(refused_lines)
            if merged != real:
                await put(path, scope, owner, name, merged, real or None)
            continue
        if name == INDEX_NAME:
            content = _without_dropped(content, stored_content, prefix, dropped)
            if content == stored_content:
                continue
        elif breach := policy_breach(
            path,
            content,
            stored_content,
            view=prefix in stored.views,
            people=people,
        ):
            dropped[path] = breach
            continue
        await put(path, scope, owner, name, content, stored_content)
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
        if prefix in read_only:
            dropped[path] = _TEAM_READ_ONLY
            continue
        if prefix in stored.views:
            if breach := policy_breach(path, None, stored.contents[path], view=True):
                dropped[path] = breach
            continue
        scope, owner = wanted[prefix]
        try:
            await store.delete(
                project_id=project_id,
                scope=scope,
                owner_handle=owner,
                path=name,
                expected_version=stored.versions.get(path),
                updated_by=updated_by,
                writer=writer,
            )
        except (MemoryFileConflict, NotFoundError):
            conflict.append(path)
            continue
        before[path] = stored.contents[path]
        removed.append(path)
    return MemoryChange(
        added=tuple(sorted(added)),
        updated=tuple(sorted(updated)),
        removed=tuple(sorted(removed)),
        conflicted=tuple(sorted(set(conflict))),
        refused=refused,
        rejected=rejected,
        dropped=dropped,
        unindexed=unindexed,
        diffs=_diffs(before, written),
    )


def _kept_talk(prefix: str, stored: StoredTree, files: dict) -> set[str]:
    """一棵多人地方的 private 在这一次之后有的 communication 记录（文件名）：铺下
    去的那几条，加这一次新记、平台收得下的那几条。"""
    kept = {
        path[len(prefix) + 1 :]
        for path in stored.contents
        if path.startswith(f"{prefix}/") and not path.endswith(f"/{INDEX_NAME}")
    }
    for path, content in files.items():
        if (
            isinstance(content, str)
            and path.startswith(f"{prefix}/")
            and path not in stored.contents
            and not path.endswith(f"/{INDEX_NAME}")
            and policy_breach(path, content, None, view=True) is None
        ):
            kept.add(path[len(prefix) + 1 :])
    return kept


def _names(prefix: str, paths) -> set[str]:
    return {p[len(prefix) + 1 :] for p in paths if p.startswith(f"{prefix}/")}


def _view_index_reason(lines: list[str]) -> str:
    shown = "\n".join(f"  {line}" for line in lines[:5])
    return (
        "多人的地方里，索引只收新记的 communication 记录的那一行；"
        f"下面这几处改动没有保存：\n{shown}"
    )


def _without_dropped(
    index: str, before: str | None, prefix: str, dropped: dict[str, str]
) -> str:
    """索引这一次新加的、指向这一次没收的新记录的那几行，不收：索引和它新指向
    的记录同生同死（`tree._without_dangling` 在会话机那一侧做同一件事）。"""
    old = set((before or "").splitlines())
    gone = _names(prefix, dropped)
    if not gone:
        return index
    kept = [
        line
        for line in index.splitlines()
        if line in old
        or not (entries := parse_index(line))
        or entries[0].path not in gone
    ]
    return "\n".join(kept) + ("\n" if index.endswith("\n") else "")


async def apply_withdrawn(
    session: AsyncSession,
    project_id: uuid.UUID,
    withdrawn: object,
    *,
    updated_by: str,
    views: bool = False,
    people: dict[str, str] | None = None,
) -> tuple[MemoryChange, list[tuple[MemoryFileScope, str | None]], dict[str, str]]:
    """会话交回来的那几棵没点名的 private 树（``withdrawn``：前缀 → 它在磁盘上的
    ``files``、它那一段 ``baseline`` 和 ``bases``、树上的旁路文件 ``sidecars``），
    合进它们主人的那一份。

    合的规矩和 `sync_tree` 在会话那一侧对点名的树用的是同一条，只是平台这一份
    此刻才读：会话改过、平台没动的收下；两边都动过的，索引按行合并，别的平台赢。
    返回改了什么、收了哪几个作用域（说给谁听用），和那个人写下、却没有收进去的
    那几版（路径 → 正文）：被平台盖回去的、超了上限的、树上还留着的旁路文件。
    在点了名的树上它们留在会话目录里给写它的 agent 重读；这一棵已经离开了那个座
    位，它们只能交给本人（`queries._say_handed_back`）。

    ``views``：交回来的是多人地方铺下的那一份（`files.talk_view`），按同样的规矩
    读、合、收。那里没收的写入（别的类型、改已有的记录）不交给本人：它们本就不该
    写进他的 private，说给写它的 agent（`MemoryChange.dropped`）。"""
    if not isinstance(withdrawn, dict):
        return MemoryChange(), [], {}
    scopes: list[tuple[MemoryFileScope, str | None]] = []
    disk: dict[str, str] = {}
    baseline: dict[str, str] = {}
    bases: dict[str, str] = {}
    kept: dict[str, str] = {}
    for prefix, given in withdrawn.items():
        parts = str(prefix).split("/")
        if (
            len(parts) != 2
            or parts[0] != PRIVATE_PREFIX
            or parts[1] in ("", ".", "..")
            or not isinstance(given, dict)
        ):
            continue
        scopes.append((MemoryFileScope.private, parts[1]))
        for name, content in (given.get("files") or {}).items():
            try:
                check_path(str(name))
            except MemoryFileError:
                continue
            if isinstance(content, str):
                disk[f"{prefix}/{name}"] = content
        for table, into in (
            (given.get("baseline"), baseline),
            (given.get("bases"), bases),
        ):
            for path, value in (table or {}).items():
                if str(path).startswith(f"{prefix}/") and isinstance(value, str):
                    into[str(path)] = value
        for name, content in (given.get("sidecars") or {}).items():
            if (
                isinstance(content, str)
                and "/" not in str(name)
                and not (views and str(name).endswith(".rejected.md"))
            ):
                kept[f"{prefix}/{name}"] = content
    if not scopes:
        return MemoryChange(), [], {}
    stored = await read_tree(
        session,
        project_id,
        scopes,
        views=frozenset(prefix_of_scope(*scope) for scope in scopes)
        if views
        else frozenset(),
    )
    merged = sync_tree(
        scopes=stored.scopes,
        disk=disk,
        baseline=baseline,
        bases=bases,
        views=stored.views,
        people=people,
    )
    change = await apply_tree(
        session,
        project_id,
        stored,
        {
            "files": merged.files,
            "refused": merged.refused,
            "rejected": merged.rejected,
            "unindexed": list(merged.unindexed),
        },
        scopes=scopes,
        updated_by=updated_by,
        # 交回来的是那个人在一轮对话里写下的 private，只是那一轮的座位已经换了人。
        writer=MemoryWriter.turn,
        people=people,
    )
    kept.update({path: body for path, body in change.refused.items() if body})
    if views:
        # 没收下的那几条说给写它的 agent，不交给本人。
        change = replace(
            change, dropped={**change.rejected, **change.dropped}, rejected={}
        )
    else:
        kept.update({path: disk[path] for path in change.rejected if path in disk})
    return change, scopes, kept


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
