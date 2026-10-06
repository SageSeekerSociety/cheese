"""旧表搬迁的那条路：先出报告，人点头，才落笔。

纯模块管的是「这份计划成立吗」（`tests/unit/test_old_memory_moves_into_files.py`）。
这里管的是**顺序和边界**，也就是只有真的接上库、接上会话才看得见的那几件事：

- dry-run 一个字都不写进 `memory_files`（报告先给人看，看的是它）；
- 点头只认 `settings.memory_migration_reviewer` 这一个人，apply 只认 approved；
- 落笔动的是新树，**旧表在这条路上只读**（搬完不删，删表是 30 天后的另一个迁移）；
- 报告出来之后有人改过那棵树、或者旧记忆本身变了，整次都不写：报告描述的已经不
  是当下的东西了，重跑 dry-run。
"""

import json

import pytest
from sqlalchemy import func, select, update

from app.core.config import settings
from app.core.errors import BadRequestError, ConflictError, ForbiddenError
from app.domain.gateway_chat import Completion, Usage
from app.domain.memory.files import MemoryFileScope, prefix_of
from app.domain.memory.files_store import MemoryFileStore
from app.domain.memory.migration_service import MemoryMigrationService
from app.domain.memory.models import (
    MemoryEntry,
    MemoryMigrationPlan,
    MemoryMigrationStatus,
    MemoryScope,
    user_scope_id,
)
from app.domain.project.services import ProjectService
from tests.integration.conftest import registered
from tests.support.living_doc import write_overview

pytestmark = pytest.mark.anyio

_DOC = """## 目标

做一套课程推荐。

## 大家都该知道的

分页接口用 cursor，不用 offset。

## 现在在做什么

这一节不是来源。
"""


class _Model:
    """一个照念答案的模型：测试把决定摆好，它原样吐回来。

    `asked` 留着，是为了「报告是一次问出来的，不是分几次拼的」这类断言——分批问
    的时候每一批只看得到自己那几条旧记忆，批数和内容都是这一层的形状。
    """

    model = "fake-migration-model"

    def __init__(self, decisions: list[dict]) -> None:
        self.decisions = decisions
        self.asked: list[str] = []

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        timeout: float,
        json_response: bool = False,
    ) -> Completion:
        self.asked.append(prompt)
        return Completion(
            json.dumps({"decisions": self.decisions}, ensure_ascii=False), Usage()
        )


async def _project(session, handle: str = "alice"):
    await registered(session, handle)
    project = await ProjectService(session).create(
        name="旧记忆搬迁", owner_handle=handle
    )
    await session.flush()
    return project


async def _old(session, project, content: str, *, person: str | None = None):
    """往旧表里放一条：某个 agent 在这个项目里学到的，或者它关于某个人的认识。"""
    row = MemoryEntry(
        scope=MemoryScope.user if person else MemoryScope.agent_project,
        scope_id=(
            user_scope_id(project.id, "芝士", person)
            if person
            else f"{project.id}:芝士"
        ),
        content=content,
    )
    session.add(row)
    await session.flush()
    return row


async def _old_rows(session, project_id) -> list[str]:
    """旧表现在有什么。搬完之后要一模一样——这条路对它是只读的。"""
    rows = await session.scalars(
        select(MemoryEntry)
        .where(MemoryEntry.scope_id.startswith(f"{project_id}:", autoescape=True))
        .order_by(MemoryEntry.id)
    )
    return [row.content for row in rows.all()]


async def _team_files(session, project_id) -> dict[str, str]:
    """新树 team 那一半：路径 → 正文。"""
    rows = await MemoryFileStore(session).list(project_id, MemoryFileScope.team)
    return {row.path: row.content for row in rows}


async def _private_files(session, project_id, owner: str) -> dict[str, str]:
    rows = await MemoryFileStore(session).list(
        project_id, MemoryFileScope.private, owner
    )
    return {row.path: row.content for row in rows}


def _decision(source: str, destination: str, reason: str = "因为它在这儿", **fields):
    return {"source": source, "destination": destination, "reason": reason, **fields}


# --- 报告先出，字一个不写 ---------------------------------------------------


async def test_the_report_comes_before_any_write(business_db_factory):
    async with business_db_factory() as session:
        project = await _project(session)
        entry = await _old(session, project, "发版前先跑一遍 make e2e")
        model = _Model(
            [
                _decision(
                    f"entry:{entry.id}",
                    "team",
                    path="run-e2e-before-release",
                    type="project",
                    description="发版前跑 make e2e",
                    body="发版前先跑一遍 make e2e。",
                )
            ]
        )
        plan = await MemoryMigrationService(session, chat=model).dry_run(
            project.id, by="alice"
        )
        await session.commit()

        assert plan.status == MemoryMigrationStatus.draft.value
        # 报告里那条路写得出来（哪一条、去哪儿、为什么），但树上一个文件都还没有。
        assert "发版前先跑一遍 make e2e" in plan.report
        assert "team/run-e2e-before-release.md" in plan.report
        assert "因为它在这儿" in plan.report
        assert await _team_files(session, project.id) == {}
        assert await _old_rows(session, project.id) == ["发版前先跑一遍 make e2e"]
        # 旧记忆原样存着：apply 之前拿它算一次指纹。
        assert plan.sources_digest
        assert [item["content"] for item in plan.sources] == ["发版前先跑一遍 make e2e"]


async def test_what_the_migration_asks_is_the_platforms_spend(business_db_factory):
    """搬迁是平台自己做的事（#2233）：花销记在平台头上，项目所在的团队一分不扣。"""
    from app.domain.usage.ledger import Ledger, payer_for_project
    from app.domain.usage.models import ResourceUsage

    class _Billed(_Model):
        async def complete(self, **kwargs) -> Completion:
            answer = await super().complete(**kwargs)
            return Completion(answer.content, Usage(500, 40), 0.01)

    async with business_db_factory() as session:
        project = await _project(session)
        entry = await _old(session, project, "发版前先跑一遍 make e2e")
        model = _Billed(
            [
                _decision(
                    f"entry:{entry.id}",
                    "team",
                    path="run-e2e-before-release",
                    type="project",
                    description="发版前跑 make e2e",
                    body="发版前先跑一遍 make e2e。",
                )
            ]
        )
        await MemoryMigrationService(session, chat=model).dry_run(
            project.id, by="alice"
        )
        await session.commit()

        rows = list(await session.scalars(select(ResourceUsage)))
        assert [
            (r.kind, r.input_tokens, r.output_tokens, r.cost_usd, r.team_id)
            for r in rows
        ] == [("memory_migration", 500, 40, 0.01, None)]
        payer = await payer_for_project(session, project.id)
        assert (await Ledger(session).balance(payer)).credits_used == 0


async def test_a_project_with_nothing_left_to_move_has_no_report(business_db_factory):
    async with business_db_factory() as session:
        project = await _project(session)
        with pytest.raises(BadRequestError):
            await MemoryMigrationService(session, chat=_Model([])).dry_run(
                project.id, by="alice"
            )


async def test_without_a_model_there_is_no_report(business_db_factory):
    """判去处的是模型。没有模型的时候不能出一份「全部丢掉」的报告。"""
    async with business_db_factory() as session:
        project = await _project(session)
        await _old(session, project, "一条旧记忆")
        # No gateway to mint a key on, so no model to ask.
        service = MemoryMigrationService(session)
        with pytest.raises(BadRequestError):
            await service.dry_run(project.id, by="alice")


async def test_a_plan_that_breaks_the_hard_rules_is_refused_whole(
    business_db_factory,
):
    """「关于某人的」池子里的东西，模型说要进 team —— 整份计划不成立。

    不是跳过那一条、剩下的照搬：那会让「每一条都有去处」变成「大多数有去处」，
    而漏掉的那一条没人看得见。
    """
    async with business_db_factory() as session:
        project = await _project(session)
        entry = await _old(session, project, "他习惯用 vim", person="alice")
        model = _Model(
            [
                _decision(
                    f"entry:{entry.id}",
                    "team",
                    path="prefers-vim",
                    type="user",
                    description="他习惯用 vim",
                    body="他习惯用 vim。",
                )
            ]
        )
        with pytest.raises(BadRequestError):
            await MemoryMigrationService(session, chat=model).dry_run(
                project.id, by="alice"
            )
        await session.commit()

        assert await _team_files(session, project.id) == {}
        assert (
            await session.scalars(select(MemoryMigrationPlan.source_ids))
        ).all() == []


# --- 人点头才算数 -----------------------------------------------------------


async def test_only_the_named_reviewer_says_yes(business_db_factory):
    async with business_db_factory() as session:
        project = await _project(session)
        entry = await _old(session, project, "一条旧记忆")
        plan = await MemoryMigrationService(
            session,
            chat=_Model([_decision(f"entry:{entry.id}", "discard", "过时了")]),
        ).dry_run(project.id, by="alice")
        service = MemoryMigrationService(session)

        with pytest.raises(ForbiddenError):
            await service.approve(plan.id, by="alice")
        with pytest.raises(ForbiddenError):
            await service.approve(plan.id, by="芝士")

        reviewer = settings.memory_migration_reviewer
        approved = await service.approve(plan.id, by=reviewer)
        await session.commit()

        assert approved.status == MemoryMigrationStatus.approved.value
        assert approved.approved_by == reviewer


async def test_nothing_lands_before_the_yes(business_db_factory):
    async with business_db_factory() as session:
        project = await _project(session)
        entry = await _old(session, project, "发版前先跑一遍 make e2e")
        plan = await MemoryMigrationService(
            session,
            chat=_Model(
                [
                    _decision(
                        f"entry:{entry.id}",
                        "team",
                        path="run-e2e-before-release",
                        type="project",
                        description="发版前跑 make e2e",
                        body="发版前先跑一遍 make e2e。",
                    )
                ]
            ),
        ).dry_run(project.id, by="alice")
        await session.commit()
        plan_id = plan.id

    async with business_db_factory() as session:
        service = MemoryMigrationService(session)
        with pytest.raises(ForbiddenError):
            await service.apply(plan_id, by=settings.memory_migration_reviewer)
        assert await _team_files(session, project.id) == {}


# --- 点头之后：新树变了，旧表没动 -------------------------------------------


async def test_the_approved_plan_lands_and_the_old_table_stays_put(
    business_db_factory,
):
    async with business_db_factory() as session:
        project = await _project(session)
        await write_overview(session, project.id, _DOC, "alice")
        store = MemoryFileStore(session)
        await store.write(
            project_id=project.id,
            scope=MemoryFileScope.team,
            owner_handle=None,
            path="pagination.md",
            content="# pagination\n\n分页那一套。\n",
            updated_by="alice",
            expected_version=None,
        )
        await store.write(
            project_id=project.id,
            scope=MemoryFileScope.team,
            owner_handle=None,
            path="MEMORY.md",
            content="- [pagination](pagination.md) — 分页那一套\n",
            updated_by="alice",
            expected_version=None,
        )
        agent = await _old(session, project, "发版前先跑一遍 make e2e")
        person = await _old(session, project, "他习惯用 vim", person="alice")
        docish = await _old(
            session, project, "构建命令是 make e2e（写在 CLAUDE.md 里）"
        )
        before = await _old_rows(session, project.id)
        model = _Model(
            [
                _decision(
                    f"entry:{agent.id}",
                    "team",
                    path="run-e2e-before-release",
                    type="project",
                    description="发版前跑 make e2e",
                    body="发版前先跑一遍 make e2e。",
                ),
                _decision(
                    f"entry:{person.id}",
                    "private",
                    owner="alice",
                    type="user",
                    path="prefers-vim",
                    description="他习惯用 vim",
                    body="他习惯用 vim。",
                ),
                _decision(
                    "overview:大家都该知道的#1",
                    "merge",
                    "总览里已经有一句分页的话，并进去",
                    path="pagination",
                    body="游标是 cursor，不是 offset。",
                ),
                _decision(
                    f"entry:{docish.id}",
                    "suggest",
                    "这是构建命令，不是记忆",
                    target="CLAUDE.md",
                    body="发版前跑 make e2e。",
                ),
            ]
        )
        service = MemoryMigrationService(session, chat=model)
        plan = await service.dry_run(project.id, by="alice")
        project_id = project.id
        assert plan.suggestions[0]["target"] == "CLAUDE.md"
        assert "CLAUDE.md" in plan.report
        await service.approve(plan.id, by=settings.memory_migration_reviewer)
        await session.commit()
        plan_id = plan.id

    async with business_db_factory() as session:
        row = await MemoryMigrationService(session).apply(
            plan_id, by=settings.memory_migration_reviewer
        )
        await session.commit()

        assert row.status == MemoryMigrationStatus.applied.value
        assert row.applied_at is not None
        assert "4 条旧记忆 → 3 个文件" in row.summary
        assert "suggest 1" in row.summary

        team = await _team_files(session, project_id)
        # 新开的那一条在，带 frontmatter。
        assert "name: run-e2e-before-release" in team["run-e2e-before-release.md"]
        assert "发版前先跑一遍 make e2e。" in team["run-e2e-before-release.md"]
        # 并进去的那一条：原文件的 frontmatter 一个字不动，正文接在后面。
        assert team["pagination.md"].startswith("# pagination")
        assert "游标是 cursor，不是 offset。" in team["pagination.md"]
        # 索引里多了新文件那一行，已经有的一行不重复。
        assert team["MEMORY.md"].count("pagination.md") == 1
        assert (
            "[run-e2e-before-release](run-e2e-before-release.md)" in team["MEMORY.md"]
        )

        private = await _private_files(session, project_id, "alice")
        assert "name: prefers-vim" in private["prefers-vim.md"]
        assert "type: user" in private["prefers-vim.md"]

        # 旧表：一条不多、一条不少、一个字没改。搬完不删，删表是 30 天后的另一次。
        assert await _old_rows(session, project_id) == before


async def test_saying_yes_twice_changes_nothing(business_db_factory):
    async with business_db_factory() as session:
        project = await _project(session)
        entry = await _old(session, project, "一条旧记忆")
        service = MemoryMigrationService(
            session,
            chat=_Model([_decision(f"entry:{entry.id}", "discard", "过时了")]),
        )
        plan = await service.dry_run(project.id, by="alice")
        await service.approve(plan.id, by=settings.memory_migration_reviewer)
        await session.commit()
        plan_id = plan.id

    async with business_db_factory() as session:
        service = MemoryMigrationService(session)
        await service.apply(plan_id, by=settings.memory_migration_reviewer)
        await session.commit()

    async with business_db_factory() as session:
        service = MemoryMigrationService(session)
        # 已经搬过的那一份不能再搬一次：report 里那几条旧记忆已经不在树上了。
        with pytest.raises(ForbiddenError):
            await service.apply(plan_id, by=settings.memory_migration_reviewer)
        with pytest.raises(ConflictError):
            await service.approve(plan_id, by=settings.memory_migration_reviewer)


async def test_a_second_report_does_not_move_what_already_moved(business_db_factory):
    """搬过的那几条不再出现在下一次的旧记忆清单里。

    否则第二次搬迁会在新树里长出一份重名的东西——而重名意味着两条记忆抢一个文件。
    """
    async with business_db_factory() as session:
        project = await _project(session)
        first = await _old(session, project, "第一条")
        second = await _old(session, project, "第二条")
        model = _Model(
            [
                _decision(
                    f"entry:{first.id}",
                    "team",
                    path="the-first",
                    type="project",
                    description="第一条",
                    body="第一条。",
                ),
                _decision(
                    f"entry:{second.id}",
                    "team",
                    path="the-second",
                    type="project",
                    description="第二条",
                    body="第二条。",
                ),
            ]
        )
        service = MemoryMigrationService(session, chat=model)
        plan = await service.dry_run(project.id, by="alice")
        await service.approve(plan.id, by=settings.memory_migration_reviewer)
        await session.commit()
        plan_id = plan.id

    async with business_db_factory() as session:
        await MemoryMigrationService(session).apply(
            plan_id, by=settings.memory_migration_reviewer
        )
        await session.commit()

    async with business_db_factory() as session:
        # 两条都搬过了：没有还没搬过的旧记忆，也就没有第二份报告。
        with pytest.raises(BadRequestError):
            await MemoryMigrationService(session, chat=_Model([])).dry_run(
                project.id, by="alice"
            )
        # 树还是那两份，没长出第二份同名的东西。
        team = await _team_files(session, project.id)
        assert sorted(team) == ["MEMORY.md", "the-first.md", "the-second.md"]


# --- 报告和现实对不上：整次都不写 -------------------------------------------


async def _one_file_plan(business_db_factory, *, handle: str = "alice"):
    async with business_db_factory() as session:
        project = await _project(session, handle)
        entry = await _old(session, project, "发版前先跑一遍 make e2e")
        service = MemoryMigrationService(
            session,
            chat=_Model(
                [
                    _decision(
                        f"entry:{entry.id}",
                        "team",
                        path="run-e2e-before-release",
                        type="project",
                        description="发版前跑 make e2e",
                        body="发版前先跑一遍 make e2e。",
                    )
                ]
            ),
        )
        plan = await service.dry_run(project.id, by=handle)
        await service.approve(plan.id, by=settings.memory_migration_reviewer)
        await session.commit()
        return project.id, plan.id, plan.files[0], plan.sources_digest


async def test_a_change_to_the_tree_between_yes_and_write_stops_the_whole_thing(
    business_db_factory,
):
    """复核那几分钟里有人写了同一份文件：写下去就是覆盖他的改动。

    冲突是拒绝，不是合并——落在这一层就是「整次都没写」，而不是「跳过那一个」。
    """
    project_id, plan_id, planned, _ = await _one_file_plan(business_db_factory)

    async with business_db_factory() as session:
        # 复核人点头之后、落笔之前，另一个人改了那条记忆。
        await MemoryFileStore(session).write(
            project_id=project_id,
            scope=MemoryFileScope(planned["scope"]),
            owner_handle=planned["owner"] or None,
            path=planned["path"],
            content="# run-e2e-before-release\n\n另一个人写的一版。\n",
            updated_by="bob",
            expected_version=planned["version"],
        )
        await session.commit()

    async with business_db_factory() as session:
        service = MemoryMigrationService(session)
        with pytest.raises(ConflictError):
            await service.apply(plan_id, by=settings.memory_migration_reviewer)
        await session.commit()

    async with business_db_factory() as session:
        row = await MemoryMigrationService(session).get_or_404(plan_id)
        assert row.status == MemoryMigrationStatus.failed.value
        assert "复核之后有人改过这棵树" in row.summary
        team = await _team_files(session, project_id)
        # 只有 bob 那一版；搬迁那一份一个字都没进去，索引也没动。
        assert team == {
            planned["path"]: "# run-e2e-before-release\n\n另一个人写的一版。\n"
        }


async def test_a_change_to_the_old_memories_between_yes_and_write_stops_it(
    business_db_factory,
):
    """复核期间旧表里那条被退休了：这份报告描述的不是现在的旧表了，重跑。"""
    project_id, plan_id, planned, _ = await _one_file_plan(
        business_db_factory, handle="carol"
    )

    async with business_db_factory() as session:
        # 退休那一条：`live_entries()` 之后它就不在「旧记忆」里了。
        await session.execute(
            update(MemoryEntry)
            .where(MemoryEntry.scope_id == f"{project_id}:芝士")
            .values(retired_at=func.now())
        )
        await session.commit()

    async with business_db_factory() as session:
        service = MemoryMigrationService(session)
        with pytest.raises(ConflictError):
            await service.apply(plan_id, by=settings.memory_migration_reviewer)
        await session.commit()

    async with business_db_factory() as session:
        row = await MemoryMigrationService(session).get_or_404(plan_id)
        assert row.status == MemoryMigrationStatus.failed.value
        assert "重跑 dry-run" in row.summary
        assert await _team_files(session, project_id) == {}


async def test_the_reviewer_reads_the_plan_back_before_apply(business_db_factory):
    """计划存下来是为了让人读回去：接口和脚本都是读这一份，不再问一次模型。"""
    project_id, plan_id, _, digest = await _one_file_plan(
        business_db_factory, handle="dave"
    )

    async with business_db_factory() as session:
        row = await MemoryMigrationService(session).get_or_404(plan_id)
        assert row.project_id == project_id
        assert row.sources_digest == digest
        assert len(row.sources) == 1
        assert row.sources[0]["source_id"].startswith("entry:")
        assert row.sources[0]["origin"] == "agent"
        assert row.report.startswith("# 旧记忆迁移报告")
        assert row.decisions and row.files and row.indexes
        assert row.created_by == "dave"
        assert row.approved_by == settings.memory_migration_reviewer
        # 作用域和前缀是同一件事的两种写法，读的人不该在两处之间猜。
        assert f"{prefix_of(MemoryFileScope.team, None)}/" in row.report
