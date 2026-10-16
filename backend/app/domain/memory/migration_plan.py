"""一份迁移计划：一条旧记忆去哪儿、要写哪些文件、报告上写着什么。

输入是两样——旧记忆（`migration_sources.OldEntry`）和模型给出的那串决定
（`decode_answer`）；输出是 `MigrationPlan`：要写的文件、要改的索引、只建议不自动
改的那些、以及那份给人复核的报告（`render_report`，`MigrationPlan.report()` 就是
它）。

**全是纯的**：没有数据库、没有 LLM。所以「每一条都有去处」「private 不许升级进
team」这两条可以在单测里直接问，不用先跑一次模型。

这一层判的是「这份计划搬不搬得动」：`build_plan` 之前，模型已经给每条旧记忆派了去
处，这里逐条校验（去处不合法、缺理由、目标文件不存在、private 升级进 team……），再
把同一个文件的几条决定合成一份内容。每一条拒绝都点名**是哪一条、哪里不对**——一次
迁移要人复核，读报告的人（和改 prompt 的人）要能从那句话里知道下一步动什么，而不是
只知道「失败了」。

问模型的那段话在 `migration_prompt.py`；重导出这些名字的入口在 `migration.py`。
"""

import json
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.sentences import listing, say
from app.domain.memory.files import (
    INDEX_LINE_MAX,
    INDEX_NAME,
    MemoryFile,
    MemoryFileError,
    MemoryFileScope,
    MemoryType,
    check_path,
    limit_breach,
    parse_index,
    prefix_of,
    valid_name,
)
from app.domain.memory.migration_sources import OldEntry, Source, sources_digest

#: 迁移后 team 的 L1 索引目标行数。**是目标，不是闸**：超了照样出报告，但报告上
#: 要红着写出来——这次迁移的意义之一就是把索引压回去，压不回去得让人看见。
#: 上限本身是 200 行（`files.INDEX_MAX_LINES`），那是「读不读得到」的线。
TEAM_INDEX_GOAL_LINES = 120

#: 一条理由最多多少字。
REASON_MAX = 200

#: 一次迁移里，报告本身要留在哪。给人看的落点是项目总览房间，这份报告是附件。
REPORT_TITLE = "旧记忆迁移报告"

_MERGE_SEPARATOR = "\n\n"


class Destination(StrEnum):
    """一条旧记忆的五种去处。**每一条都要有一个**，没有第六种「先放着」。

    ``merge`` 和 ``team`` / ``private`` 分开，是因为它们写的动作不一样：前者是
    往一条已经存在的记忆上补，后者是新开一条。报告要让人看出「这一版新开了 40
    个文件」和「这一版把 40 条并进了 12 个已有的」是两件事。
    """

    team = "team"
    private = "private"
    merge = "merge"
    #: 只建议写进 `CLAUDE.md` / `SKILL.md`，**一个字都不自动改**：那不是记忆的地
    #: 方，改它要另一次人的决定。
    suggest = "suggest"
    discard = "discard"


#: 每个去处允许的 `type`。`user` 永远 private（「这个人是谁」不是项目共识），
#: team 里也不该出现它——那正是「private 不许升级进 team」在类型上的样子。`merge`
#: 不在这张表里：并进去的那一段跟着目标文件自己的 type，不从这一条上取。
_ALLOWED_TYPES: dict[Destination, tuple[MemoryType, ...]] = {
    Destination.team: (
        MemoryType.project,
        MemoryType.reference,
        MemoryType.feedback,
    ),
    Destination.private: (MemoryType.user, MemoryType.feedback),
}


class MigrationError(ValueError):
    """这份计划搬不动：模型给的东西不合法，或者缺了谁的去处。

    理由里带上**是哪一条**、**哪里不对**：一次迁移要人复核，读报告的人（和改
    prompt 的人）要能从这句话里知道下一步动什么，而不是只知道「失败了」。
    """


@dataclass(frozen=True)
class Decision:
    """一条旧记忆的去处。"""

    source_id: str
    destination: Destination
    reason: str
    #: 新文件的 slug（`team/<path>.md` / `private/<owner>/<path>.md`），或 `merge`
    #: 的目标文件名。空的时候是它自己的 slug 都没给——`_check` 会拒。
    path: str = ""
    #: `private` / `merge` 的主人。
    owner: str = ""
    #: `merge` 的目标作用域：空串 = team。
    scope: MemoryFileScope | None = None
    type: MemoryType | None = None
    description: str = ""
    body: str = ""
    #: `suggest` 的落点：`CLAUDE.md` / `SKILL.md` / 别的文件名。
    target: str = ""

    def target_file(self) -> tuple[MemoryFileScope, str, str]:
        """这条决定要写进哪个文件：`(作用域, 主人, 文件名)`。"""
        owner = self.owner if self.scope is MemoryFileScope.private else None
        return (self.scope or MemoryFileScope.team), (owner or ""), f"{self.path}.md"

    def target_prefix(self) -> str:
        """这条决定动的是哪棵树：`team` 或 `private/<handle>`。"""
        return prefix_of(self.target_file()[0], self.target_file()[1])

    def target_path(self) -> str:
        """这条决定动的是哪个文件（带作用域前缀、带 `.md`）。`suggest` 没有目标
        文件——它说的是一份文档，那不是一个记忆文件，所以这里给空串。"""
        if self.destination is Destination.suggest:
            return ""
        scope, owner, name = self.target_file()
        return f"{prefix_of(scope, owner)}/{name}"

    def index_line(self) -> str:
        """这一条在索引里的那一行。

        `description` 是空的时候退回正文的头一句：索引里一行钩子都不给，等于这个
        文件在索引里没有名字。`description` 本身在校验时就要过，这里只是为了这
        个方法自己站得住。
        """
        hook = " ".join(self.description.split()) or " ".join(self.body.split())
        head = f"- [{self.path}]({self.path}.md) — "
        return head + hook[: max(INDEX_LINE_MAX - len(head), 0)]


@dataclass(frozen=True)
class PlannedFile:
    """要写下去的一个文件：整份新内容，和读到的那一版。"""

    scope: MemoryFileScope
    owner: str
    path: str
    content: str
    #: dry-run 时读到的那一版；新建是 None。apply 带上它，对不上就是有人在这期间
    #: 改过——那一条冲突会让整次搬迁停下来（见 `migration_service.apply`）。
    version: int | None
    #: 这份文件装着哪几条旧记忆。
    sources: tuple[str, ...] = ()
    #: 新建（True）还是并进已有的（False）。
    is_new: bool = True

    @property
    def prefixed(self) -> str:
        """报告和 diff 里用的完整路径（`team/x.md`）。"""
        return f"{prefix_of(self.scope, self.owner)}/{self.path}"


@dataclass(frozen=True)
class PlannedIndex:
    """一个作用域的 `MEMORY.md` 新内容。索引和正文分开写，因为它们的冲突面不一样。"""

    scope: MemoryFileScope
    owner: str
    content: str
    version: int | None
    #: 这一次新加的行数（报告用来说明索引是怎么涨的）。
    added_lines: int = 0


@dataclass(frozen=True)
class Suggestion:
    """一条只建议、不自动改的东西。"""

    source_id: str
    target: str
    text: str
    reason: str


@dataclass(frozen=True)
class MigrationPlan:
    """一次搬迁的全部内容：报告、要写的文件、和它算出来的账。"""

    project_id: str
    sources: tuple[OldEntry, ...]
    decisions: tuple[Decision, ...]
    files: tuple[PlannedFile, ...]
    indexes: tuple[PlannedIndex, ...]
    suggestions: tuple[Suggestion, ...]
    #: 旧表那几条的指纹。apply 前对一次：`dry-run` 之后旧表又被人写过，说明这份
    #: 报告描述的已经不是现在的旧表了，得重跑。
    sources_digest: str = ""
    #: 每条旧记忆的原样（模型的输入）。重建计划要用它，报告不用。
    raw_decisions: tuple[dict, ...] = field(default=())

    def team_index_lines(self) -> int:
        """迁移后 team 索引有多少行。"""
        for index in self.indexes:
            if index.scope is MemoryFileScope.team:
                return _line_count(index.content)
        return 0

    def over_goal(self) -> bool:
        return self.team_index_lines() > TEAM_INDEX_GOAL_LINES

    def counts(self) -> dict[str, int]:
        """每个去处各几条。报告开头那一行。"""
        tally = {destination.value: 0 for destination in Destination}
        for decision in self.decisions:
            tally[decision.destination.value] += 1
        return tally

    def report(self) -> str:
        return render_report(self)


def _line_count(text: str) -> int:
    return len([line for line in text.splitlines() if line.strip()])


# ---------------------------------------------------------------------------
# 模型的输出 → 一份计划
# ---------------------------------------------------------------------------


def decode_answer(answer: str) -> list[dict]:
    """模型那段 JSON → 一串原始决定。

    只认两种形状：`{"decisions": [...]}` 和裸的 `[...]`。别的都是错——一个能
    「尽力而为」地解析的读取器，会在模型少写半个花括号时把大部分决定读成空，而
    空决定在这里意味着「有几条旧记忆没有去处」，那正是最不能猜的地方。
    """
    text = answer.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MigrationError(say("migrationNotJson", error=str(exc))) from exc
    if isinstance(parsed, dict):
        parsed = parsed.get("decisions")
    if not isinstance(parsed, list):
        raise MigrationError(say("migrationNoDecisions"))
    decisions: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise MigrationError(say("migrationDecisionNotObject", item=repr(item)))
        decisions.append(item)
    return decisions


def build_plan(
    *,
    project_id: str,
    entries: list[OldEntry],
    existing: dict[str, str],
    owners: list[str],
    files: list[tuple[MemoryFileScope, str, str, int]],
    raw_decisions: list[dict],
) -> MigrationPlan:
    """决定 → 完整计划（要写什么 + 报告）。

    `existing` 是路径（`team/x.md`）→ 正文，`files` 是每个作用域现有哪些文件
    （作用域, 主人, 文件名, 版本）——包括那份 `MEMORY.md`。两者都由调用方从
    `memory_files` 读出来，这里只看不读。`owners` 是**可以收 private 记忆的人**：
    一棵还没写过的 `private/<handle>` 树是可以现建的，一个不在名单上的人不是
    ——那是把记忆写给一个不存在的人。
    """
    by_id = {entry.source_id: entry for entry in entries}
    if len(by_id) != len(entries):
        raise MigrationError(say("migrationDuplicateSource"))
    decisions = _decisions(raw_decisions, by_id=by_id, owners=owners, files=files)
    planned, indexes, suggestions = _lay_out(
        decisions=decisions, existing=existing, files=files
    )
    return MigrationPlan(
        project_id=project_id,
        sources=tuple(entries),
        decisions=tuple(decisions),
        files=tuple(planned),
        indexes=tuple(indexes),
        suggestions=tuple(suggestions),
        sources_digest=sources_digest(entries),
        raw_decisions=tuple(raw_decisions),
    )


def _decisions(
    raw: list[dict],
    *,
    by_id: dict[str, OldEntry],
    owners: list[str],
    files: list[tuple[MemoryFileScope, str, str, int]],
) -> list[Decision]:
    """一串原始决定 → 校验过的决定，**一条旧记忆都不许漏**。

    `keep` 是「能写的地方」：`team` 永远可以（一个项目的 team 树是它本来就有的
    那一棵，还没写过就是空的），`private/<handle>` 要么这个人已经在名单上、要么
    他已经有一棵树。别的一律拒——写给一个不存在的人，和写错地方是同一种错。
    """
    keep = {"team"} | {f"private/{owner}" for owner in owners}
    keep |= {prefix_of(scope, owner) for scope, owner, _, _ in files}
    known_files = {
        f"{prefix_of(scope, owner)}/{name}" for scope, owner, name, _ in files
    }
    decisions: list[Decision] = []
    seen: set[str] = set()
    for item in raw:
        decision = _one(item, by_id=by_id, owners=owners, keep=keep)
        if decision.source_id in seen:
            raise MigrationError(
                say("migrationTwoDestinations", source=decision.source_id)
            )
        seen.add(decision.source_id)
        target = decision.target_path()
        if decision.destination is Destination.merge and target not in known_files:
            raise MigrationError(
                say(
                    "migrationMergeTargetMissing",
                    source=decision.source_id,
                    target=target,
                )
            )
        if (
            decision.destination in (Destination.team, Destination.private)
            and target in known_files
        ):
            # 新建一条撞上已经存在的文件，落笔就是**整份覆盖**——把那条记忆连同
            # 别人写进去的东西一起换掉。这次迁移没有「覆盖」这个动作：要动一条已经
            # 存在的记忆，只有 merge。
            raise MigrationError(
                say("migrationCreateExists", source=decision.source_id, target=target)
            )
        decisions.append(decision)
    missing = sorted(set(by_id) - seen)
    if missing:
        raise MigrationError(
            say(
                "migrationUnplaced",
                count=len(missing),
                sources=listing(missing[:10]),
                more="…" if len(missing) > 10 else "",
            )
        )
    return decisions


def _one(
    item: dict,
    *,
    by_id: dict[str, OldEntry],
    owners: list[str],
    keep: set[str],
) -> Decision:
    source_id = str(item.get("source") or item.get("source_id") or "").strip()
    if source_id not in by_id:
        raise MigrationError(say("migrationUnknownSource", source=repr(source_id)))
    entry = by_id[source_id]
    raw_destination = str(item.get("destination") or "").strip().lower()
    try:
        destination = Destination(raw_destination)
    except ValueError as exc:
        raise MigrationError(
            say(
                "migrationDestinationInvalid",
                source=source_id,
                destination=repr(raw_destination),
            )
        ) from exc
    reason = " ".join(str(item.get("reason") or "").split())[:REASON_MAX]
    if not reason:
        raise MigrationError(say("migrationNoReason", source=source_id))
    scope: MemoryFileScope | None = None
    if destination is Destination.private:
        scope = MemoryFileScope.private
    elif destination is Destination.merge:
        scope = (
            MemoryFileScope.private
            if str(item.get("scope") or "").strip().lower() == "private"
            else MemoryFileScope.team
        )
    owner = str(item.get("owner") or "").strip()
    if scope is MemoryFileScope.private and owner not in owners:
        raise MigrationError(
            say(
                "migrationOwnerNotInMigration",
                source=source_id,
                owner=owner or say("migrationOwnerUnnamed"),
            )
        )
    type_name = str(item.get("type") or "").strip().lower()
    try:
        kind = MemoryType(type_name) if type_name else None
    except ValueError as exc:
        raise MigrationError(
            say("migrationTypeInvalid", source=source_id, type=repr(type_name))
        ) from exc
    decision = Decision(
        source_id=source_id,
        destination=destination,
        reason=reason,
        path=_slug(item, destination=destination, source_id=source_id),
        owner=owner,
        scope=scope,
        type=kind,
        description=" ".join(str(item.get("description") or "").split()),
        body=str(item.get("body") or "").strip(),
        target=str(item.get("target") or "").strip(),
    )
    _check(decision, entry=entry, keep=keep)
    return decision


def _slug(item: dict, *, destination: Destination, source_id: str) -> str:
    """`path` / `name` 都当 slug 收，`.md` 后缀写了也认。"""
    raw = str(item.get("path") or item.get("name") or "").strip()
    if raw.endswith(".md"):
        raw = raw[: -len(".md")]
    if raw.upper() == INDEX_NAME[: -len(".md")]:
        raise MigrationError(
            say("migrationIntoIndex", source=source_id, index=INDEX_NAME)
        )
    if raw and not valid_name(raw):
        raise MigrationError(
            say("migrationNameNotKebab", source=source_id, name=repr(raw))
        )
    if not raw and destination in (
        Destination.team,
        Destination.private,
        Destination.merge,
    ):
        raise MigrationError(say("migrationNoFileName", source=source_id))
    return raw


def _check(decision: Decision, *, entry: OldEntry, keep: set[str]) -> None:
    """一条决定的硬规矩。三条，每条都对应一次真出过的错。"""
    if entry.origin is Source.person and decision.destination in (
        Destination.team,
        Destination.merge,
    ):
        if decision.target_prefix() == "team":
            raise MigrationError(
                say("migrationPersonalIntoTeam", source=decision.source_id)
            )
    if decision.destination is Destination.suggest and not decision.target:
        raise MigrationError(say("migrationSuggestNoTarget", source=decision.source_id))
    if decision.destination is Destination.discard:
        return
    if decision.destination is Destination.suggest:
        return
    if decision.destination is Destination.merge:
        # 并进去的那一段要有正文；目标文件的 type 说了算，不从这一条上取。
        if not decision.body:
            raise MigrationError(say("migrationMergeNoBody", source=decision.source_id))
        return
    if decision.type is None:
        raise MigrationError(say("migrationNoType", source=decision.source_id))
    allowed = _ALLOWED_TYPES[decision.destination]
    if decision.type not in allowed:
        raise MigrationError(
            say(
                "migrationTypeWrongPlace",
                source=decision.source_id,
                type=decision.type.value,
                destination=decision.destination.value,
            )
        )
    if not decision.description:
        raise MigrationError(say("migrationNoDescription", source=decision.source_id))
    if not decision.body:
        raise MigrationError(say("migrationNoBody", source=decision.source_id))
    prefix = decision.target_prefix()
    if prefix not in keep:
        raise MigrationError(
            say("migrationScopeMissing", source=decision.source_id, scope=prefix)
        )


def _check_path(path: str) -> None:
    try:
        check_path(path)
    except MemoryFileError as exc:
        raise MigrationError(str(exc)) from exc


# ---------------------------------------------------------------------------
# 决定 → 要写的文件
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def render_report(plan: MigrationPlan) -> str:
    """这份迁移的报告。**它就是人复核的那份东西**，所以每一节都得能自己站住。"""
    by_id = {entry.source_id: entry for entry in plan.sources}
    lines: list[str] = [
        f"# {REPORT_TITLE}",
        "",
        f"- 项目：`{plan.project_id}`",
        f"- 旧记忆：**{len(plan.sources)} 条**，全部有去处",
        f"- 要写：**{len(plan.files)} 个文件**（新建 "
        f"{sum(1 for f in plan.files if f.is_new)} 个、合并 "
        f"{sum(1 for f in plan.files if not f.is_new)} 个），"
        f"索引加 {sum(index.added_lines for index in plan.indexes)} 行",
        f"- 只建议、不自动改：{len(plan.suggestions)} 条",
        "",
        "## team 的索引 {#index}",
        "",
        f"迁移后 **{plan.team_index_lines()} 行**（目标 ≤ {TEAM_INDEX_GOAL_LINES} 行）"
        + (" —— **没达标，还要再合并一批。**" if plan.over_goal() else " —— 达标。"),
        "",
        "## 一条一条的去处 {#each}",
        "",
        "| 旧记忆 | 从哪儿来 | 去处 | 目标 | 一句话理由 |",
        "|---|---|---|---|---|",
    ]
    for decision in plan.decisions:
        entry = by_id[decision.source_id]
        target = decision.target or decision.target_path() or "—"
        lines.append(
            f"| `{entry.excerpt(60)}` | {entry.where} | {decision.destination.value} "
            f"| `{target}` | {decision.reason} |"
        )
    lines += ["", "## 谁扔了什么 {#counts}", ""]
    counts = plan.counts()
    lines.append(
        "、".join(f"{name} {count} 条" for name, count in counts.items() if count)
        or "（没有决定）"
    )
    lines += ["", "## 要写的文件 {#files}", ""]
    for planned in plan.files:
        verb = "新建" if planned.is_new else "合并（追加正文）"
        lines += [
            f"### `{planned.prefixed}`（{verb}，{len(planned.sources)} 条旧记忆）",
            "",
            "```markdown",
            planned.content.rstrip(),
            "```",
            "",
        ]
    if plan.suggestions:
        lines += [
            "## 只是建议，不自动改 {#suggest}",
            "",
            "下面这些不属于记忆。要动它们，是另一件要人点头的事。",
            "",
        ]
        for suggestion in plan.suggestions:
            entry = by_id[suggestion.source_id]
            text = suggestion.text or f"把「{entry.excerpt()}」写进去"
            lines.append(f"- `{suggestion.target}`：{text}（{suggestion.reason}）")
        lines.append("")
    discarded = [
        decision
        for decision in plan.decisions
        if decision.destination is Destination.discard
    ]
    if discarded:
        lines += ["## 丢掉 {#discard}", ""]
        for decision in discarded:
            entry = by_id[decision.source_id]
            lines.append(f"- `{entry.excerpt()}` —— {decision.reason}")
        lines += [
            "",
            "丢掉的是**不再搬**：旧表一个字都不删（它留着只读），这一条只是不变成新"
            "树里的一条记忆。",
            "",
        ]
    lines += [
        "## 下一步 {#next}",
        "",
        "复核这份报告、点了头之后才会写进 `memory_files`。承认之前，一条都不用改。",
        "",
    ]
    return "\n".join(lines)
