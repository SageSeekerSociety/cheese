"""记忆整理: what one pass may change, and what it must leave alone.

The behaviour these pin down is the reason a pass is allowed to rewrite memory
unattended at all — it never deletes, it never overwrites a fact that moved
under it, and it cannot reach a pool it does not read. Break any one of those
and the failure is silent: memory that looks organized and is missing something.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.domain.memory.dream import apply_dream, revert_dream
from app.domain.memory.models import MemoryEntry, MemoryScope
from app.domain.memory.store import DbMemoryStore
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService

pytestmark = pytest.mark.anyio


async def _project_topic(session, name="P"):
    project = await ProjectService(session).create(name=name, owner_handle="u")
    topic = await TopicService(session).create(
        project_id=project.id, title="T", created_by="u"
    )
    return project, topic


async def _remember(session, project_id, *facts) -> list[uuid.UUID]:
    """Seed the project's shared pool — one of the two pools a pass may touch."""
    store = DbMemoryStore(session)
    for fact in facts:
        await store.remember(MemoryScope.project, str(project_id), fact)
    rows = (
        await session.scalars(
            select(MemoryEntry)
            .where(MemoryEntry.scope_id == str(project_id))
            .order_by(MemoryEntry.created_at)
        )
    ).all()
    return [r.id for r in rows]


async def _facts(session, project_id) -> list[str]:
    return await DbMemoryStore(session).recall(
        MemoryScope.project, str(project_id), limit=100
    )


async def test_a_merge_replaces_its_sources_with_one_line(client):
    async with client.test_factory() as session:
        project, topic = await _project_topic(session)
        a, b, c = await _remember(
            session, project.id, "沙箱里没有 docker", "沙箱没装 docker", "无关的一条"
        )

        result = await apply_dream(
            session,
            topic_id=topic.id,
            snapshot_at=datetime.now(UTC),
            merges=[{"replaces": [str(a), str(b)], "content": "沙箱里没有 docker"}],
            drops=[],
            adds=[],
            summary="合了两条重复的",
        )
        await session.commit()

        assert result.retired == 2
        assert result.added == 1
        assert result.skipped == []
        assert sorted(await _facts(session, project.id)) == sorted(
            ["沙箱里没有 docker", "无关的一条"]
        )
        # The sources are retired, not gone, and grouped by the pass — which is
        # what makes the undo below possible.
        for entry_id in (a, b):
            row = await session.get(MemoryEntry, entry_id)
            assert row is not None and row.retired_at is not None
            assert row.retired_by == result.dream_id
        assert (await session.get(MemoryEntry, c)).retired_at is None


async def test_a_fact_touched_after_the_snapshot_is_left_alone(client):
    """The organizer yields to the writer. A fact edited while 芝士 was thinking
    is one nobody proposed anything about — applying the proposal to it anyway
    would silently throw away the newer version."""
    async with client.test_factory() as session:
        project, topic = await _project_topic(session)
        a, b = await _remember(session, project.id, "旧的说法", "另一条")
        snapshot = datetime.now(UTC)
        await session.execute(
            update(MemoryEntry)
            .where(MemoryEntry.id == a)
            .values(content="有人刚改过", updated_at=snapshot + timedelta(minutes=5))
        )
        await session.commit()

        result = await apply_dream(
            session,
            topic_id=topic.id,
            snapshot_at=snapshot,
            merges=[],
            drops=[str(a), str(b)],
            adds=[],
        )
        await session.commit()

        assert result.retired == 1
        assert [s["entry"] for s in result.skipped] == [str(a)]
        assert "快照" in result.skipped[0]["reason"]
        # The concurrent write survives untouched; the rest of the pass applied.
        assert await _facts(session, project.id) == ["有人刚改过"]


async def test_a_merge_whose_sources_all_moved_adds_nothing(client):
    """The dangerous half of yielding: writing the merged line while retiring
    none of its sources would ADD a duplicate — the exact thing being cleaned
    up — and it would look like the pass succeeded."""
    async with client.test_factory() as session:
        project, topic = await _project_topic(session)
        a, b = await _remember(session, project.id, "重复一", "重复二")
        snapshot = datetime.now(UTC)
        await session.execute(
            update(MemoryEntry)
            .where(MemoryEntry.id.in_([a, b]))
            .values(updated_at=snapshot + timedelta(minutes=1))
        )
        await session.commit()

        result = await apply_dream(
            session,
            topic_id=topic.id,
            snapshot_at=snapshot,
            merges=[{"replaces": [str(a), str(b)], "content": "合并后的一条"}],
            drops=[],
            adds=[],
        )
        await session.commit()

        assert (result.added, result.retired) == (0, 0)
        assert len(result.skipped) == 2
        assert sorted(await _facts(session, project.id)) == ["重复一", "重复二"]


async def test_ids_that_are_gone_or_already_retired_are_skipped_not_fatal(client):
    """A proposal is a batch. One stale id must not throw away the other edits
    芝士 spent a turn working out."""
    async with client.test_factory() as session:
        project, topic = await _project_topic(session)
        (a,) = await _remember(session, project.id, "真实存在的一条")
        first = await apply_dream(
            session,
            topic_id=topic.id,
            snapshot_at=None,
            merges=[],
            drops=[str(a)],
            adds=[],
        )
        await session.commit()
        assert first.retired == 1

        result = await apply_dream(
            session,
            topic_id=topic.id,
            snapshot_at=None,
            merges=[],
            drops=[str(a), str(uuid.uuid4()), "这根本不是个 id"],
            adds=["一条新收割的事实"],
        )
        await session.commit()

        assert result.retired == 0
        assert result.added == 1
        reasons = [s["reason"] for s in result.skipped]
        assert len(reasons) == 3
        assert any("已经被退休" in r for r in reasons)
        assert any("不存在" in r for r in reasons)
        assert any("不是合法" in r for r in reasons)


async def test_a_pass_cannot_reach_another_projects_memory(client):
    """Entry ids are global handles, so "which pool" has to be enforced rather
    than assumed. Without this a topic could retire another project's memory by
    naming a UUID it has no way of having seen."""
    async with client.test_factory() as session:
        mine, topic = await _project_topic(session, name="Mine")
        theirs, _ = await _project_topic(session, name="Theirs")
        await _remember(session, mine.id, "我的记忆")
        (victim,) = await _remember(session, theirs.id, "别人的记忆")

        result = await apply_dream(
            session,
            topic_id=topic.id,
            snapshot_at=None,
            merges=[],
            drops=[str(victim)],
            adds=[],
        )
        await session.commit()

        assert result.retired == 0
        assert [s["reason"] for s in result.skipped] == ["不属于这个话题能整理的记忆池"]
        assert await _facts(session, theirs.id) == ["别人的记忆"]


async def test_undoing_a_pass_puts_the_pool_back(client):
    async with client.test_factory() as session:
        project, topic = await _project_topic(session)
        a, b = await _remember(session, project.id, "第一条", "第二条")
        before = sorted(await _facts(session, project.id))

        result = await apply_dream(
            session,
            topic_id=topic.id,
            snapshot_at=None,
            merges=[{"replaces": [str(a), str(b)], "content": "合并后"}],
            drops=[],
            adds=["顺手加的一条"],
        )
        await session.commit()
        assert sorted(await _facts(session, project.id)) == ["合并后"]

        undone = await revert_dream(session, result.dream_id)
        await session.commit()

        assert undone == {"reverted": True, "restored": 2, "retired": 2}
        assert sorted(await _facts(session, project.id)) == before
        # Idempotent: undoing twice must not resurrect what the pass added.
        assert (await revert_dream(session, result.dream_id))["reverted"] is False
        assert sorted(await _facts(session, project.id)) == before


async def test_retired_facts_leave_recall_count_and_search(client):
    """The whole soft-delete is worthless if any read forgets the filter: an
    unfiltered query quietly reinstates every fact 芝士 decided was wrong."""
    async with client.test_factory() as session:
        project, topic = await _project_topic(session)
        (a,) = await _remember(session, project.id, "PostgreSQL 跑在 5433 端口")
        await _remember(session, project.id, "前端用 Vue 3")
        store = DbMemoryStore(session)
        assert await store.count(MemoryScope.project, str(project.id)) == 2
        assert (
            len(await store.search(MemoryScope.project, str(project.id), "5433")) == 1
        )

        await apply_dream(
            session,
            topic_id=topic.id,
            snapshot_at=None,
            merges=[],
            drops=[str(a)],
            adds=[],
        )
        await session.commit()

        assert await store.recall(MemoryScope.project, str(project.id)) == [
            "前端用 Vue 3"
        ]
        assert await store.count(MemoryScope.project, str(project.id)) == 1
        assert await store.search(MemoryScope.project, str(project.id), "5433") == []


async def test_new_facts_land_in_the_agents_own_pool(client):
    """`adds` go where `cheese remember` puts things, so where a fact lives does
    not depend on whether a human turn or a pass wrote it."""
    async with client.test_factory() as session:
        project, topic = await _project_topic(session)

        await apply_dream(
            session,
            topic_id=topic.id,
            snapshot_at=None,
            merges=[],
            drops=[],
            adds=["整理时收割到的事实"],
        )
        await session.commit()

        row = await session.scalar(
            select(MemoryEntry).where(MemoryEntry.content == "整理时收割到的事实")
        )
        assert row is not None
        assert row.scope == MemoryScope.agent_project
        assert row.scope_id.startswith(f"{project.id}:")
        # ...and it is not in the legacy shared pool.
        assert await _facts(session, project.id) == []
