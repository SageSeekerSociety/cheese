"""决定 → 要写的文件与索引。

`_lay_out` 把一条条决定码成一份份**整份内容**：同一个文件的几条决定合起来写一次
（新文件的正文按顺序拼，合并的追加到原文件后面），索引行按路径去重。它只算、不
写——写下去以后会怎样（版本冲突、事务）在 `migration_service.apply` 里。
"""

from __future__ import annotations

from app.core.sentences import listing, say
from app.domain.memory.files import (
    INDEX_NAME,
    MemoryFile,
    MemoryFileError,
    MemoryFileScope,
    MemoryType,
    check_path,
    limit_breach,
    parse_index,
    prefix_of,
)

from .plan import (
    Decision,
    Destination,
    MigrationError,
    PlannedFile,
    PlannedIndex,
    Suggestion,
)

_MERGE_SEPARATOR = "\n\n"


def _check_path(path: str) -> None:
    try:
        check_path(path)
    except MemoryFileError as exc:
        raise MigrationError(str(exc)) from exc


def _lay_out(
    *,
    decisions: list[Decision],
    existing: dict[str, str],
    files: list[tuple[MemoryFileScope, str, str, int]],
) -> tuple[list[PlannedFile], list[PlannedIndex], list[Suggestion]]:
    """把一条条决定码成一份份文件。

    **同一个文件的几条决定合起来写一次**：模型把两条旧记忆派到同一个新文件、或者
    派到同一条已经存在的记忆上，都是正常的（同一件事本来就有好几个说法）。落到磁
    盘上只有一次写入，所以这里先合：新文件的正文按顺序拼，合并的正文追加到原文件
    后面。写完的文件是整份内容（不是补丁）——`MemoryFileStore.write` 要的就是整份。
    """
    versions = {
        f"{prefix_of(scope, owner)}/{name}": (scope, owner, name, version)
        for scope, owner, name, version in files
    }
    new_files: dict[str, list[Decision]] = {}
    merges: dict[str, list[Decision]] = {}
    suggestions: list[Suggestion] = []
    for decision in decisions:
        if decision.destination in (Destination.team, Destination.private):
            scope, owner, name = decision.target_file()
            _check_path(name)
            where = f"{prefix_of(scope, owner)}/{name}"
            new_files.setdefault(where, []).append(decision)
        elif decision.destination is Destination.merge:
            merges.setdefault(decision.target_path(), []).append(decision)
        elif decision.destination is Destination.suggest:
            suggestions.append(
                Suggestion(
                    source_id=decision.source_id,
                    target=decision.target,
                    text=decision.body,
                    reason=decision.reason,
                )
            )
    planned: dict[str, PlannedFile] = {}
    added: dict[tuple[MemoryFileScope, str], list[str]] = {}
    for path, group in {**new_files, **merges}.items():
        if path in new_files:
            scope, owner, name = group[0].target_file()
            content = _render_new(group)
            version = versions.get(path, (scope, owner, name, None))[3]
            added.setdefault((scope, owner), []).append(group[0].index_line())
        else:
            # merge 的目标在 `_decisions` 里已经确认存在，这里一定读得到它的版本。
            scope, owner, name, version = versions[path]
            content = _append_to(existing.get(path, ""), group)
        if breach := limit_breach(name, content, existing.get(path)):
            raise MigrationError(
                say(
                    "migrationFileTooBig",
                    path=path,
                    sources=listing(decision.source_id for decision in group),
                    breach=breach,
                )
            )
        planned[path] = PlannedFile(
            scope=scope,
            owner=owner,
            path=name,
            content=content,
            version=version,
            sources=tuple(decision.source_id for decision in group),
            is_new=path in new_files,
        )
    indexes: list[PlannedIndex] = []
    for scope, owner in added:
        index_path = f"{prefix_of(scope, owner)}/{INDEX_NAME}"
        # 一棵还没写过的树也有它的索引：版本是 None（新建），作用域和主人来自这
        # 棵树本身——退给 team 会把某个人的索引写成项目的索引。
        version = versions.get(index_path, (scope, owner, INDEX_NAME, None))[3]
        lines = added[(scope, owner)]
        current = existing.get(index_path, "")
        content, count = _with_lines(current, lines)
        indexes.append(
            PlannedIndex(
                scope=scope,
                owner=owner,
                content=content,
                version=version,
                added_lines=count,
            )
        )
    return (
        sorted(planned.values(), key=lambda item: item.prefixed),
        sorted(indexes, key=lambda item: prefix_of(item.scope, item.owner)),
        suggestions,
    )


def _render_new(group: list[Decision]) -> str:
    """一个新文件的整份内容。几条决定并进同一个文件时，正文按顺序拼。

    `name` 取那条决定的 slug（不是文件名）：frontmatter 里的 `name` 和索引里那一
    行指的是同一个短名，带上 `.md` 之后读它的人会以为那是一个文件名。
    """
    head = group[0]
    body = _MERGE_SEPARATOR.join(decision.body for decision in group if decision.body)
    return MemoryFile(
        name=head.path,
        description=head.description,
        type=head.type or MemoryType.project,
        body=body,
    ).text()


def _append_to(current: str, group: list[Decision]) -> str:
    """并进一条已经存在的记忆：正文接在后面，frontmatter 一个字不动。

    不动 frontmatter 是有意的——`name` / `description` / `type` 是那条记忆的元
    信息，而这次并进去的是一段补充。改它等于让这一条记忆换了身份，而报告上写的
    是「并进」，不是「改写」。
    """
    body = _MERGE_SEPARATOR.join(decision.body for decision in group if decision.body)
    text = current.rstrip()
    return f"{text}{_MERGE_SEPARATOR}{body}\n" if text else f"{body}\n"


def _with_lines(current: str, lines: list[str]) -> tuple[str, int]:
    """把几行指针加进索引；已经在里面的不重复加。

    先跑一遍解析再比路径，而不是字符串包含：一行 `- [x](x.md) — …` 和另一行
    `- [x](x-e.md) — …` 之间，子串判据是分不出来的。
    """
    known = {entry.path for entry in parse_index(current)}
    fresh = [line for line in lines if _line_path(line) not in known]
    if not fresh:
        return current, 0
    text = current.rstrip()
    joined = "\n".join(fresh)
    return (f"{text}\n{joined}\n" if text else f"{joined}\n"), len(fresh)


def _line_path(line: str) -> str:
    for entry in parse_index(line):
        return entry.path
    return ""
