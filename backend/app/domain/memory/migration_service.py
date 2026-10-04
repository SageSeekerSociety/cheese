"""搬迁的那一步：读、问模型、存报告、等人点头、落笔。

规矩在 `migration.py`（纯的），这里只做它做不了的三件事：

1. **读三处来源**：旧表 `memory_entries` 的两个池、总览文档里那两节、以及新树现
   在的样子（`memory_files`）。读到什么决定了报告上有什么。
2. **问一次模型**：去处是模型判的（一条旧记忆该进 project 还是某个人的 private，靠
   读正文），分批问（`MIGRATION_CHUNK`），问完把报告和计划**存下来**——人复核的
   是这一份，所以 apply 重放它，不再问第二次。
3. **落笔**：`apply` 只写 `memory_files`，一次事务，一条冲突就整次不写（冲突是
   拒绝，不是合并；`MemoryFileStore.write` 已经把这个规矩实现了）。

**旧表在这条路上是只读的。** 搬完不删、不改、不标记：删表是另一个迁移，要等搬完
看一阵（30 天）再决定。所以这里对 `memory_entries` 只有一次 `SELECT`。

**人点头才算数。** `approve` 只认 `settings.memory_migration_reviewer` 这一个人，
`apply` 只认已经 approved 的计划。`apply` 之前还会核一次旧记忆的指纹：复核这几分
钟里总览文档被谁改过，这份报告描述的就不是现在的旧表了，得重跑 dry-run。
"""

import logging
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.core.sentences import exception_text, say
from app.domain.agent.announce import announce
from app.domain.agent.platform_notices import (
    EVENT_MEMORY_ORGANIZING,
    SEVERITY_INFO,
    SEVERITY_WARN,
    WHO_PLATFORM,
    notice,
)
from app.domain.gateway_chat import GatewayCallError, GatewayChat
from app.domain.identity.handles import agent_instance_handle
from app.domain.membership.roster import roster
from app.domain.memory.files import MemoryFileScope, prefix_of
from app.domain.memory.files_store import (
    MemoryFileConflict,
    MemoryFileStore,
    private_owners,
)
from app.domain.memory.migration import (
    MIGRATION_SYSTEM,
    MigrationError,
    MigrationPlan,
    OldEntry,
    Source,
    build_plan,
    decode_answer,
    migration_prompt,
    overview_entries,
    sources_digest,
)
from app.domain.memory.models import (
    MemoryEntry,
    MemoryMigrationPlan,
    MemoryMigrationStatus,
    MemoryScope,
    parse_user_scope_id,
)
from app.domain.memory.store import live_entries
from app.domain.project.services import ProjectService
from app.domain.service_keys import KeySpec, service_key
from app.domain.topic.services import TopicService
from app.domain.usage.ledger import Ledger

logger = logging.getLogger(__name__)

#: 写进 `memory_files.updated_by` 的名字。它回答的是「这一版是谁写的」，而这一版
#: 是搬迁写的——不是某个人、也不是某个 agent 的判断。写成某个人的 handle，那条
#: 记忆就有了一个它没说过话的作者。
MIGRATION_AUTHOR = "memory-migration"
#: ``resource_usage.kind`` of the calls a migration makes.
USAGE_KIND = "memory_migration"

#: 报告进房间时最多带多少字。房间那句话是给人扫一眼的，整份报告（可能几十 KB）
#: 留在 `memory_migration_plans.report` 里，接口和脚本都读得到。
REPORT_IN_ROOM_MAX = 6000


@dataclass(frozen=True)
class Gathered:
    """搬迁要读的东西，一次读齐。"""

    project_id: uuid.UUID
    project_name: str
    root_topic_id: uuid.UUID | None
    entries: list[OldEntry]
    owners: list[str]
    #: 路径（`project/x.md`）→ 正文。
    existing: dict[str, str]
    #: `(作用域, 主人, 文件名, 版本)`——包括每个作用域的 `MEMORY.md`。
    files: list[tuple[MemoryFileScope, str, str, int]]


class MemoryMigrationService:
    """一个请求一个实例，跟着那一个 session 走。"""

    def __init__(self, session: AsyncSession, *, chat: GatewayChat | None = None):
        self._session = session
        self._chat = chat

    async def _model(self) -> GatewayChat | None:
        """The model that judges where each old memory goes. The platform runs
        this migration, so it calls on a key of its own with its own budget."""
        if self._chat is None:
            key = await service_key(
                self._session,
                KeySpec(
                    name="memory-migration-gateway-key",
                    alias="memory-migration",
                    model=settings.memory_migration_model,
                    budget_usd=settings.memory_migration_budget_usd,
                    rpm=60,
                ),
            )
            if key is not None:
                self._chat = GatewayChat(
                    key, settings.memory_migration_model, max_tokens=8192
                )
        return self._chat

    # --- 读 -------------------------------------------------------------

    async def gather(self, project_id: uuid.UUID) -> Gathered:
        """三处来源读成一份。"""
        project = await ProjectService(self._session).get_or_404(project_id)
        rows = list(
            (
                await self._session.scalars(
                    select(MemoryEntry)
                    .where(
                        MemoryEntry.scope.in_(
                            [MemoryScope.agent_project, MemoryScope.user]
                        ),
                        MemoryEntry.scope_id.startswith(
                            f"{project_id}:", autoescape=True
                        ),
                        live_entries(),
                    )
                    .order_by(MemoryEntry.created_at, MemoryEntry.id)
                )
            ).all()
        )
        agent = (
            agent_instance_handle(project.default_agent_instance_id)
            if project.default_agent_instance_id
            else "芝士"
        )
        moved = await self._already_moved(project_id)
        entries: list[OldEntry] = []
        people: set[str] = set()
        for row in rows:
            person = parse_user_scope_id(row.scope_id)
            where = (
                f"池：{person[1]} 关于 {person[2]} 的"
                if person
                else f"池：{agent} 在这个项目里学到的"
            )
            if person:
                people.add(person[2])
            source_id = f"entry:{row.id}"
            if source_id in moved:
                continue
            entries.append(
                OldEntry(
                    source_id=source_id,
                    origin=Source.person if person else Source.agent,
                    where=where,
                    content=row.content,
                )
            )
        if project.root_topic_id is not None:
            doc = await TopicService(self._session).get_doc(project.root_topic_id)
            for entry in overview_entries(doc.content if doc else ""):
                if entry.source_id not in moved:
                    entries.append(entry)
        # 「这个项目里有谁」只有 `roster()` 一个读法（`test_one_roster_agents_
        # included` 守着这件事）：直接读 `people()` 是第二份名册。私人记忆的主人
        # 只能是**人**，所以这里把名册上那些 `agent=True` 的席位滤掉——它们是队友，
        # 而队友没有 `private/<handle>`。
        members = await roster(self._session, project_id)
        owners = sorted(
            people
            | {member.handle for member in members if not member.agent}
            | set(await private_owners(self._session, project_id))
        )
        files, existing = await self._read_tree(project_id, owners)
        return Gathered(
            project_id=project_id,
            project_name=project.name,
            root_topic_id=project.root_topic_id,
            entries=entries,
            owners=owners,
            existing=existing,
            files=files,
        )

    async def _read_tree(
        self, project_id: uuid.UUID, owners: list[str]
    ) -> tuple[list[tuple[MemoryFileScope, str, str, int]], dict[str, str]]:
        """新树现在的样子：每个作用域的文件名和版本，以及它们的正文。"""
        store = MemoryFileStore(self._session)
        files: list[tuple[MemoryFileScope, str, str, int]] = []
        existing: dict[str, str] = {}
        scopes: list[tuple[MemoryFileScope, str | None]] = [
            (MemoryFileScope.project, None),
            *[(MemoryFileScope.private, owner) for owner in owners],
        ]
        for scope, owner in scopes:
            for row in await store.list(project_id, scope, owner):
                files.append(
                    (
                        scope,
                        owner or "",
                        row.path,
                        row.version,
                    )
                )
                existing[f"{prefix_of(scope, owner)}/{row.path}"] = row.content
        return files, existing

    async def _already_moved(self, project_id: uuid.UUID) -> set[str]:
        """搬过的那几条。第二次 dry-run 不能再搬一遍——新树里会长出重名的东西。"""
        rows = await self._session.scalars(
            select(MemoryMigrationPlan.source_ids).where(
                MemoryMigrationPlan.project_id == project_id,
                MemoryMigrationPlan.status == MemoryMigrationStatus.applied.value,
            )
        )
        moved: set[str] = set()
        for source_ids in rows.all():
            moved.update(source_ids or [])
        return moved

    # --- 报告 -----------------------------------------------------------

    async def dry_run(self, project_id: uuid.UUID, *, by: str) -> MemoryMigrationPlan:
        """读旧记忆、问模型、出一份计划。**这一步一个字都不写进新树。**"""
        gathered = await self.gather(project_id)
        if not gathered.entries:
            raise BadRequestError(say("migrationNothingToMove"))
        raw = await self._ask(gathered)
        try:
            plan = build_plan(
                project_id=str(project_id),
                entries=gathered.entries,
                existing=gathered.existing,
                owners=gathered.owners,
                files=gathered.files,
                raw_decisions=raw,
            )
        except MigrationError as exc:
            raise BadRequestError(
                say("migrationPlanInvalid", reason=exception_text(exc))
            ) from exc
        row = MemoryMigrationPlan(
            project_id=project_id,
            status=MemoryMigrationStatus.draft.value,
            sources_digest=plan.sources_digest,
            report=plan.report(),
            sources=[
                {
                    "source_id": entry.source_id,
                    "origin": entry.origin.value,
                    "where": entry.where,
                    "content": entry.content,
                }
                for entry in plan.sources
            ],
            source_ids=[entry.source_id for entry in plan.sources],
            decisions=list(plan.raw_decisions),
            files=[
                {
                    "scope": planned.scope.value,
                    "owner": planned.owner,
                    "path": planned.path,
                    "content": planned.content,
                    "version": planned.version,
                    "sources": list(planned.sources),
                    "is_new": planned.is_new,
                }
                for planned in plan.files
            ],
            indexes=[
                {
                    "scope": index.scope.value,
                    "owner": index.owner,
                    "content": index.content,
                    "version": index.version,
                    "added_lines": index.added_lines,
                }
                for index in plan.indexes
            ],
            suggestions=[
                {
                    "source_id": suggestion.source_id,
                    "target": suggestion.target,
                    "text": suggestion.text,
                    "reason": suggestion.reason,
                }
                for suggestion in plan.suggestions
            ],
            created_by=by,
        )
        self._session.add(row)
        await self._session.flush()
        await self._say_report(gathered, plan, row)
        return row

    async def _ask(self, gathered: Gathered) -> list[dict]:
        """分批问模型。一批里的决定只看得见这一批的旧记忆，合并时一起校验。"""
        chat = await self._model()
        if chat is None:
            raise BadRequestError(say("migrationNoModel"))
        decisions: list[dict] = []
        size = max(1, settings.memory_migration_chunk)
        for start in range(0, len(gathered.entries), size):
            batch = gathered.entries[start : start + size]
            prompt = migration_prompt(
                project_id=str(gathered.project_id),
                project_name=gathered.project_name,
                entries=batch,
                owners=gathered.owners,
                existing=gathered.existing,
            )
            try:
                answer = await chat.complete(
                    system=MIGRATION_SYSTEM,
                    prompt=prompt,
                    json_response=True,
                    timeout=settings.memory_migration_timeout_s,
                )
            except GatewayCallError as exc:
                raise BadRequestError(
                    say("migrationModelFailed", reason=exception_text(exc))
                ) from exc
            # 搬迁是平台自己做的事，花销记在平台头上，不扣任何团队（#2233）。
            await Ledger(self._session).record_platform(
                kind=USAGE_KIND,
                model=chat.model,
                input_tokens=answer.usage.prompt_tokens,
                output_tokens=answer.usage.completion_tokens,
                cost_usd=answer.cost_usd,
            )
            try:
                decisions += decode_answer(answer.content)
            except MigrationError as exc:
                raise BadRequestError(
                    say("migrationAnswerUnreadable", reason=exception_text(exc))
                ) from exc
        return decisions

    # --- 点头 -----------------------------------------------------------

    async def approve(self, plan_id: uuid.UUID, *, by: str) -> MemoryMigrationPlan:
        """复核人点头。**除了他，谁说都不算。**"""
        row = await self.get_or_404(plan_id)
        reviewer = settings.memory_migration_reviewer
        if by != reviewer:
            raise ForbiddenError(
                say("migrationReviewerOnly", reviewer=reviewer, plan=str(row.id))
            )
        if row.status != MemoryMigrationStatus.draft.value:
            raise ConflictError(say("migrationPlanAlreadyDecided", status=row.status))
        row.status = MemoryMigrationStatus.approved.value
        row.approved_by = by
        row.approved_at = datetime.now(UTC)
        await self._session.flush()
        return row

    async def get_or_404(self, plan_id: uuid.UUID) -> MemoryMigrationPlan:
        row = await self._session.get(MemoryMigrationPlan, plan_id)
        if row is None:
            raise NotFoundError(say("migrationPlanNotFound"))
        return row

    async def plans_of(self, project_id: uuid.UUID) -> list[MemoryMigrationPlan]:
        """这个项目搬过几次，新的在前。**报告不是机密**：复核的人要的是「上一次搬
        了什么」，而不是从头再问一遍模型。"""
        rows = await self._session.scalars(
            select(MemoryMigrationPlan)
            .where(MemoryMigrationPlan.project_id == project_id)
            .order_by(
                MemoryMigrationPlan.created_at.desc(), MemoryMigrationPlan.id.desc()
            )
        )
        return list(rows.all())

    # --- 落笔 -----------------------------------------------------------

    async def apply(self, plan_id: uuid.UUID, *, by: str) -> MemoryMigrationPlan:
        """按已复核的那份计划写进新树。**一次事务，一条写不进去就整次不写。**

        写之前核两件事：复核人点过头（`approved`），旧记忆还是报告描述的那一份
        （指纹）。版本那一关在 `MemoryFileStore.write` 里——dry-run 之后有人改过那
        棵树，写下去就是覆盖别人的改动，那里会拒。
        """
        row = await self.get_or_404(plan_id)
        if row.status != MemoryMigrationStatus.approved.value:
            raise ForbiddenError(
                say(
                    "migrationPlanNotReviewed",
                    status=row.status,
                    reviewer=settings.memory_migration_reviewer,
                )
            )
        gathered = await self.gather(row.project_id)
        # 指纹算的是**现在读出来的那一份**，不是报告里存着的那一份——后者和
        # `row.sources_digest` 是同一批字节，拿它比永远相等，这条检查就成了摆设。
        if sources_digest(gathered.entries) != row.sources_digest:
            row.status = MemoryMigrationStatus.failed.value
            row.summary = (
                "旧记忆在复核之后变了，这份报告已经不是当下的那份：重跑 dry-run"
            )
            await self._session.flush()
            raise ConflictError(row.summary)
        store = MemoryFileStore(self._session)
        written: list[str] = []
        try:
            # 正文先写、索引后写：半路上断了留下的状态是「文件在、索引里还没有它
            # 那一行」，而不是「索引指着一个不存在的文件」。
            for item in row.files:
                await store.write(
                    project_id=row.project_id,
                    scope=MemoryFileScope(item["scope"]),
                    owner_handle=item["owner"] or None,
                    path=item["path"],
                    content=item["content"],
                    updated_by=MIGRATION_AUTHOR,
                    expected_version=item["version"],
                )
                scope = MemoryFileScope(item["scope"])
                written.append(
                    f"{prefix_of(scope, item['owner'] or None)}/{item['path']}"
                )
            for item in row.indexes:
                await store.write(
                    project_id=row.project_id,
                    scope=MemoryFileScope(item["scope"]),
                    owner_handle=item["owner"] or None,
                    path="MEMORY.md",
                    content=item["content"],
                    updated_by=MIGRATION_AUTHOR,
                    expected_version=item["version"],
                )
        except MemoryFileConflict as exc:
            row.status = MemoryMigrationStatus.failed.value
            row.summary = f"复核之后有人改过这棵树（{exc}），整次都没写：重跑 dry-run"
            await self._session.flush()
            # 已经写进去的那几个随事务一起回滚——调用方那一个 session 就是一个
            # 事务，这里不 commit。
            raise ConflictError(row.summary) from exc
        row.status = MemoryMigrationStatus.applied.value
        row.applied_at = datetime.now(UTC)
        row.summary = self._summary_of(row)
        await self._session.flush()
        await self._say_applied(gathered, row, written)
        logger.info(
            "memory migration %s applied: %d files, %d indexes",
            row.id,
            len(row.files),
            len(row.indexes),
        )
        return row

    def _summary_of(self, row: MemoryMigrationPlan) -> str:
        """一句话交代这次搬了多少、各去哪儿了。给人看的，所以按去处数。"""
        tally = Counter(str(item.get("destination") or "?") for item in row.decisions)
        projects = sum(
            1 for item in row.files if item["scope"] == MemoryFileScope.project.value
        )
        project_lines = next(
            (
                len([line for line in item["content"].splitlines() if line.strip()])
                for item in row.indexes
                if item["scope"] == MemoryFileScope.project.value
            ),
            None,
        )
        return (
            f"{len(row.source_ids)} 条旧记忆 → {len(row.files)} 个文件"
            f"（project {projects}、private {len(row.files) - projects}），"
            + "、".join(f"{name} {count}" for name, count in sorted(tally.items()))
            + (
                f"；project 索引 {project_lines} 行"
                if project_lines is not None
                else ""
            )
        )

    # --- 房间里那两句话 -------------------------------------------------

    async def _say_report(
        self, gathered: Gathered, plan: MigrationPlan, row: MemoryMigrationPlan
    ) -> None:
        """报告好了：说进项目总览，谁的名都不点（要动手的是复核人，不是某个 agent）。"""
        detail = plan.report()
        if len(detail) > REPORT_IN_ROOM_MAX:
            detail = (
                detail[:REPORT_IN_ROOM_MAX] + f"\n\n…（整份报告在搬迁计划 {row.id} 上）"
            )
        await self._say(
            gathered,
            content=say(
                "memoryMigrationReportedOverGoal"
                if plan.over_goal()
                else "memoryMigrationReported",
                sources=len(row.source_ids),
                files=len(row.files),
            ),
            meta=notice(
                EVENT_MEMORY_ORGANIZING,
                severity=SEVERITY_WARN if plan.over_goal() else SEVERITY_INFO,
                who=WHO_PLATFORM,
                detail=detail,
                detail_label=say("labelMigrationReport"),
            ),
        )

    async def _say_applied(
        self, gathered: Gathered, row: MemoryMigrationPlan, written: list[str]
    ) -> None:
        """搬完了：说进项目总览，列出动过的文件。"""
        await self._say(
            gathered,
            content=say("memoryMigrationApplied", files=len(written)),
            meta=notice(
                EVENT_MEMORY_ORGANIZING,
                severity=SEVERITY_INFO,
                who=WHO_PLATFORM,
                detail="\n".join(f"- `{path}`" for path in written)
                + (f"\n\n{row.summary}" if row.summary else ""),
                detail_label=say("labelFilesWritten"),
            ),
        )

    async def _say(self, gathered: Gathered, *, content: str, meta: dict) -> None:
        if gathered.root_topic_id is None:
            return
        await announce(
            self._session, place_id=gathered.root_topic_id, content=content, meta=meta
        )
