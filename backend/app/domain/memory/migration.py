"""旧记忆搬进文件树：先出一份报告，人点头了才写。

记忆换过一次形态（条目池 → 文件树，见 `files.py`），旧表 `memory_entries` 留着没
删。库里那些行还在被界面读，但它们**不进上下文**：agent 每天的索引里一个字都不是
它们的。所以这件事迟早要做一次，而且只做一次——一次性的迁移，不是一条常驻的路。

三件事让这件事和「写个脚本 UPDATE 一下」差得很远：

1. **去处不是一个表，是一棵有主人的树。** 旧的一条进哪儿，取决于它是「关于某个人
   的」还是「关于这个项目的」——同一个池子里两种都有。猜错了的代价不对称：把私人
   的话写进 team，是替整个项目定了规矩，而且再也收不回来（team 是所有人共读的）。
2. **搬迁要一次看全。** 一条一条搬的脚本只能做「原样挪过去」，而旧表里同一件事往
   往躺着五六个说法不同的版本。搬进文件树时把它们合成一条，才是这次搬家真正省的
   东西——不然新树一开始就背着旧表的重复。
3. **它动的是所有人和所有芝士共读的那份记忆。** 所以规矩是先报告、后写入，报告里
   每一条旧记忆都要说出自己的去处和一句理由，人看过、点过头（`approved_by`），
   `apply` 才落笔。

这个模块是纯的：没有数据库、没有 LLM，只有「旧记忆长什么样」「决定合不合法」「新
树会写成什么样」。所以「每一条都有去处」「private 不许升级进 team」这两条可以在
单测里直接问，不用先跑一次模型。

**旧表不删。** 这次只搬，删表是另一个迁移，要等搬完看一阵（30 天）再说。
"""

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.block.notice_text import exception_text, listing, say
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

#: 总览文档里那两节。它们是第三个来源，和人 / agent 的池子同一个待遇：一条一条
#: 列出来，各自有一个去处。**含标题不含正文**——「由记忆整理迁入」那一节是上一版
#: 迁移写进去的，而它写的正是旧表里的行，所以这一节和 `memory_entries` 有重叠；
#: 重叠由人（或模型）在报告里看见，不在这里去重：两处可能已经分叉了，按字符串去重
#: 只会把分叉的那一版静默丢掉。
OVERVIEW_SECTIONS = ("大家都该知道的", "项目记忆（由记忆整理迁入）")

#: 迁移后 team 的 L1 索引目标行数。**是目标，不是闸**：超了照样出报告，但报告上
#: 要红着写出来——这次迁移的意义之一就是把索引压回去，压不回去得让人看见。
#: 上限本身是 200 行（`files.INDEX_MAX_LINES`），那是「读不读得到」的线。
TEAM_INDEX_GOAL_LINES = 120

#: 报告里一条旧记忆最多摘多少字。报告是给人扫的，不是把整张表倒出来。
EXCERPT_MAX = 160

#: 一条理由最多多少字。
REASON_MAX = 200


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

#: 一次迁移里，报告本身要留在哪。给人看的落点是项目总览房间，这份报告是附件。
REPORT_TITLE = "旧记忆迁移报告"

_MERGE_SEPARATOR = "\n\n"


class MigrationError(ValueError):
    """这份计划搬不动：模型给的东西不合法，或者缺了谁的去处。

    理由里带上**是哪一条**、**哪里不对**：一次迁移要人复核，读报告的人（和改
    prompt 的人）要能从这句话里知道下一步动什么，而不是只知道「失败了」。
    """


class Source(StrEnum):
    """一条旧记忆从哪儿来。它决定了几条硬规矩。"""

    #: `scope=agent_project`：某个 agent 在这个项目里学到的。
    agent = "agent"
    #: `scope=user`：某个 agent 对某个人的认识。**不许进 team。**
    person = "person"
    #: 总览文档里的那一节。
    overview = "overview"


@dataclass(frozen=True)
class OldEntry:
    """一条旧记忆：一个稳定的名字、一段正文、和它从哪儿来。"""

    #: 报告和决定都靠它对齐。写进报告里给人看，所以它得是人读得懂的，不是 uuid
    #: ——报告上要人复核「这条去哪儿」，而 `3f2a…` 说不出是哪一条。
    source_id: str
    origin: Source
    #: 它原来在哪儿（人话）。报告里跟 source_id 一起出现。
    where: str
    content: str

    def excerpt(self, limit: int = EXCERPT_MAX) -> str:
        """报告里那一行摘要：折成一行，超长截断。"""
        text = " ".join(self.content.split())
        return text if len(text) <= limit else text[: limit - 1] + "…"


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
# 来源：旧表和总览文档各自读成一样的东西
# ---------------------------------------------------------------------------


def overview_entries(doc: str) -> list[OldEntry]:
    """总览文档里那两节，读成一条条旧记忆。

    一节里的正文按**空行**切段：文档是给人看的，一段一件事，而段落是它唯一自带
    的结构。没有那两节就是空的——一份还没按这个结构写过的总览不该被硬读出东西来。
    """
    entries: list[OldEntry] = []
    for heading, body in _sections(doc):
        if heading not in OVERVIEW_SECTIONS:
            continue
        where = f"总览文档 · {heading}"
        for number, paragraph in enumerate(_paragraphs(body), start=1):
            entries.append(
                OldEntry(
                    source_id=f"overview:{heading}#{number}",
                    origin=Source.overview,
                    where=where,
                    content=paragraph,
                )
            )
    return entries


def _sections(doc: str) -> list[tuple[str, str]]:
    """`(标题, 正文)`，按出现顺序。只认 `##`（文档是 markdown，一节一级标题）。"""
    sections: list[tuple[str, str]] = []
    heading = ""
    lines: list[str] = []
    for line in doc.splitlines():
        if line.startswith("## "):
            if heading:
                sections.append((heading, "\n".join(lines)))
            heading = line[3:].strip()
            lines = []
            continue
        if heading:
            lines.append(line)
    if heading:
        sections.append((heading, "\n".join(lines)))
    return sections


def _paragraphs(body: str) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []
    for line in body.splitlines():
        if line.strip():
            current.append(line.rstrip())
            continue
        if current:
            paragraphs.append("\n".join(current).strip())
            current = []
    if current:
        paragraphs.append("\n".join(current).strip())
    return paragraphs


def sources_digest(entries: list[OldEntry]) -> str:
    """旧记忆这一份的指纹。`apply` 之前对一次，用来判「报告还是不是当下的那份」。"""
    material = "\n".join(
        f"{entry.source_id}\x00{entry.content}"
        for entry in sorted(entries, key=lambda item: item.source_id)
    )
    return hashlib.sha256(material.encode()).hexdigest()[:16]


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
        raise MigrationError(exception_text(exc)) from exc


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


# ---------------------------------------------------------------------------
# 问模型
# ---------------------------------------------------------------------------

#: 一次问模型的旧记忆条数上限。超了分几次问——一个项目的旧记忆可能上百条，全塞
#: 进一次调用既慢又容易让模型在中段开始敷衍。**分批只影响「这一次看见哪些旧记
#: 忆」**：一个已经存在的文件在每一批里都看得见，所以「并进哪一条」在批与批之间
#: 是一致的；只有「这一批要新建的文件」下一批看不见，于是两批可能各建一个近似的
#: 新文件。这一条由 report 上的人复核（新文件多、名字近似的会被一眼看出来）。
MIGRATION_CHUNK = 60

MIGRATION_SYSTEM = """你在把一批旧记忆搬进一套基于文件的记忆里。旧记忆是一条条独立
的句子（「条目池」时代的形态），新记忆是一条记忆一个 markdown 文件、一个作用域一份
`MEMORY.md` 索引。

你要**为每一条旧记忆指定一个去处**，然后把它写成新记忆的样子。你不是在摘要，是在
归置：同一件事在旧记忆里往往有好几个说法，把它们并进同一条。

## 五个去处（五选一，每条都必须选一个）

- `team`：新建一条**整个项目共读**的记忆。新的正文写进 `body`，`description` 写
  一句能让索引读得懂的钩子。`type` 只能是 `project`（项目里正在进行的事，代码和
  git 里读不出来的）、`reference`（外部系统的入口）或 `feedback`（全项目都该遵守
  的做法）。
- `private`：新建一条**只属于某个人**的记忆。要写 `owner`（那个人）。`type` 只能
  是 `user`（这个人是谁、懂什么）或 `feedback`（他个人的表达偏好、对他的工作方式
  的确认）。
- `merge`：并进一条**已经存在**的记忆（`existing` 里那些）。写 `path` 指向它，
  正文写进 `body`。同一件事已经有那条记忆了就并进去，别新建近似的副本。
- `suggest`：只**建议**写进 `CLAUDE.md` 或 `SKILL.md`，写 `target` 和一句话。
  你改不了它们，这里只把话留下。
- `discard`：这一条不值得变成记忆（代码里读一眼就知道、已经过期、和别的重复）。

## 规矩

1. **关于某个人的东西永远不能进 team。** 从「关于某个人」的池子来的条目，去处只
   能是 `private` / `merge`（并进那个人的文件）/ `suggest` / `discard`。
2. **项目规矩进 team，个人偏好进 private。** 这是同一条规矩的另一面：不要因为
   「反正是表扬」就把某个人的偏好写成全项目的做法。
3. **相对日期换成绝对日期**再写（「上周」→ 具体日期）。写不清的就别写。
4. `body` 是可以独立读懂的正文：把 `**Why:**`（为什么）和 `**How to apply:**`
   （什么时候用得上它）一起写出来，不要只写 `what`。
5. 人名、文件名、命令原样保留，不要改写你不确定的标识符。

## 输出

只输出一个 JSON 对象，没有别的字：

```json
{"decisions": [
  {"source": "<旧记忆的 id，原样抄>",
   "destination": "team | private | merge | suggest | discard",
   "owner": "<private 的主人，别的时候空着>",
   "path": "<文件名，kebab-case，不带 .md>",
   "scope": "team | private",
   "type": "project | reference | feedback | user",
   "description": "<索引那行的钩子，一句话>",
   "body": "<正文>",
   "target": "<suggest 的落点：CLAUDE.md / SKILL.md>",
   "reason": "<一句话：为什么放这儿>"}
]}
```

一条旧记忆一条决定，**一条都不能漏**。`discard` 也要写，理由里说清为什么不要它。
"""


def migration_prompt(
    *,
    project_id: str,
    project_name: str,
    entries: list[OldEntry],
    owners: list[str],
    existing: dict[str, str],
) -> str:
    """问模型的那一段：旧记忆一张表、现在的树一张表、规矩在 system 里。"""
    lines = [
        f"项目：{project_name}（`{project_id}`）",
        "",
        "这个项目里出现过的人（`private` 的主人只能从这里面选）："
        + ("、".join(owners) if owners else "（还没有）"),
        "",
        "## 现在这棵树里已经有的记忆",
        "",
    ]
    # 先按索引拼一份「钩子」表：模型挑 merge 目标时看的是钩子那一句，而不是整份
    # 正文——把每个文件的正文都塞进 prompt，一次调用就要多付几万个字。
    hooks: dict[str, str] = {}
    for path, text in sorted(existing.items()):
        if not path.endswith(f"/{INDEX_NAME}"):
            continue
        prefix = path[: -len(f"/{INDEX_NAME}")]
        for entry in parse_index(text):
            hooks[f"{prefix}/{entry.path}"] = entry.hook
    listed = [
        f"- `{path}` — {hooks.get(path) or '（索引里还没有它）'}"
        for path in sorted(existing)
        if not path.endswith(f"/{INDEX_NAME}")
    ]
    lines += listed or ["（这棵树还是空的）"]
    lines += ["", f"## 要归置的旧记忆（{len(entries)} 条）", ""]
    for entry in entries:
        lines += [
            f"### `{entry.source_id}`（{entry.where}）",
            "",
            entry.content.strip(),
            "",
        ]
    lines += [
        "---",
        "",
        "给上面每一条旧记忆指定去处，输出那个 JSON。",
    ]
    return "\n".join(lines)


__all__ = [
    "Destination",
    "Decision",
    "MigrationError",
    "MigrationPlan",
    "OldEntry",
    "OVERVIEW_SECTIONS",
    "PlannedFile",
    "PlannedIndex",
    "REPORT_TITLE",
    "Source",
    "Suggestion",
    "TEAM_INDEX_GOAL_LINES",
    "MIGRATION_CHUNK",
    "MIGRATION_SYSTEM",
    "build_plan",
    "decode_answer",
    "migration_prompt",
    "overview_entries",
    "render_report",
    "sources_digest",
]
