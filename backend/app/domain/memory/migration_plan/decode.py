"""模型的输出 → 校验过的一串决定，再合成一份计划。

`decode_answer` 只认两种形状的 JSON（`{"decisions": [...]}` 和裸的 `[...]`），
`build_plan` 把调用方读好的旧记忆（`OldEntry`）和这串决定合成一份
`MigrationPlan`。这一块判的是「这份计划搬不搬得动」：去处不合法、缺理由、目标文件
不存在、private 升级进 team…… 每一条拒绝都点名**是哪一条、哪里不对**，因为这次
迁移要人复核。

形状和常量在 `plan.py`，要写的文件由 `layout.py` 码，报告在 `report.py`。
"""

from __future__ import annotations

import json

from app.core.sentences import listing, say
from app.domain.memory.files import (
    INDEX_NAME,
    MemoryFileScope,
    MemoryType,
    prefix_of,
    valid_name,
)
from app.domain.memory.migration_sources import OldEntry, Source, sources_digest

from .layout import _lay_out
from .plan import (
    _ALLOWED_TYPES,
    REASON_MAX,
    Decision,
    Destination,
    MigrationError,
    MigrationPlan,
)


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
