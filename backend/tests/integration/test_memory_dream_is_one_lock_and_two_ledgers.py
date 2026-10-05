"""整理记忆（dream）的账：一把锁、两个数。

这一批测的是**平台那一侧的账**，也就是跑起来之后剩下什么：

- **一把锁。** 一个项目同时只能有一次整理（`claim` 是一次比较并交换）。两次同时
  起来只会互相覆盖对方刚写下的记忆，而这件事没有第二个地方拦得住——判据是「花了
  多少」，两个进程读到的量是一样的。
- **阈值只数「上次整理之后」的、且不是整理自己花的。** 数错了的后果不是报错，是
  整理要么再也不来（把自己算进去、永远到不了量，或者永远刚整理过），要么一直来
  （数成了别人的）。
- **拒绝执行的那一次也推进 `last_dream_at`。** 不推进，下一次巡检会立刻再跑一遍，
  一个坏掉的树会把 token 烧在一遍遍重复的拒绝上。
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.agent.chat import _output_tokens_since
from app.domain.memory import dream
from app.domain.memory.files import MemoryFileScope
from app.domain.memory.files_store import private_owners
from app.domain.memory.models import MemoryDreamRunStatus
from app.domain.project.services import ProjectService
from app.domain.usage.repositories import UsageRepository
from tests.integration.conftest import registered

pytestmark = pytest.mark.anyio

_NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


async def _project(session, handle: str = "alice"):
    await registered(session, handle)
    project = await ProjectService(session).create(name="记忆整理", owner_handle=handle)
    await session.flush()
    return project


async def _spend(session, project_id, tokens: int, *, at: datetime, kind: str = "chat"):
    from sqlalchemy import update

    from app.domain.usage.models import ResourceUsage

    row = await UsageRepository(session).add(
        project_id=project_id,
        conversation_id=None,
        model="m",
        input_tokens=0,
        output_tokens=tokens,
        cost_usd=0.0,
        kind=kind,
        turn_id=None,
    )
    # `created_at` 由 `Timestamps` 自动填，测试要的是「某个时刻花的」，所以写完
    # 就把这一行改成那个时刻：这一列是判据唯一的依据。
    await session.execute(
        update(ResourceUsage).where(ResourceUsage.id == row.id).values(created_at=at)
    )
    await session.flush()


# --- 阈值只数该数的那些 -----------------------------------------------------


async def test_only_what_happened_since_the_last_dream_counts(business_db_factory):
    async with business_db_factory() as session:
        project = await _project(session)
        await _spend(session, project.id, 700, at=_NOW - timedelta(days=2))
        await _spend(session, project.id, 300, at=_NOW - timedelta(hours=1))
        await session.commit()

        assert (
            await _output_tokens_since(session, project.id, _NOW - timedelta(days=1))
            == 300
        )
        assert await _output_tokens_since(session, project.id, dream.EPOCH) == 1000


async def test_the_dreams_own_spend_does_not_count(business_db_factory):
    """整理自己那一轮花的，不能算进下一次的判据。

    算进去的话，一个项目只要整理过一次就离下一次更近了一点，而整理本身不产出任何
    记忆——它会越跑越勤，最后变成「一直在整理」。`kind` 是唯一认得出来的东西：
    网关的用量是延迟落库的，一条晚到的整理用量时间戳可能落在新窗口里。
    """
    async with business_db_factory() as session:
        project = await _project(session)
        await _spend(session, project.id, 5000, at=_NOW, kind=dream.DREAM_KIND)
        await _spend(session, project.id, 5000, at=_NOW, kind="chat")
        await session.commit()

        assert await _output_tokens_since(session, project.id, dream.EPOCH) == 5000


# --- 一把锁 -----------------------------------------------------------------


async def test_a_second_dream_on_the_same_project_is_turned_away(
    business_db_factory,
):
    async with business_db_factory() as session:
        project = await _project(session)
        first = await dream.claim(session, project.id, now=_NOW)
        await session.commit()

    async with business_db_factory() as session:
        assert first is not None
        # 别人这时也想整理这个项目：抢不到，且不是排队——下一次巡检还会来。
        assert await dream.claim(session, project.id, now=_NOW) is None


async def test_the_lock_expires_so_one_interrupted_dream_does_not_own_a_project(
    business_db_factory,
):
    """占着锁的那个进程没了（重启、机器掉了），锁得自己过期。"""
    async with business_db_factory() as session:
        project = await _project(session)
        await dream.claim(session, project.id, now=_NOW)
        await session.commit()
        project_id = project.id

    async with business_db_factory() as session:
        assert (
            await dream.claim(
                session, project_id, now=_NOW + dream.CLAIM_TTL + timedelta(minutes=1)
            )
            is not None
        )


async def test_two_projects_do_not_share_one_lock(business_db_factory):
    async with business_db_factory() as session:
        one = await _project(session, "alice")
        two = await _project(session, "bob")
        assert await dream.claim(session, one.id, now=_NOW) is not None
        assert await dream.claim(session, two.id, now=_NOW) is not None
        await session.commit()


# --- 跑完之后那两个数 -------------------------------------------------------


async def test_finishing_moves_the_threshold_origin_and_frees_the_lock(
    business_db_factory,
):
    async with business_db_factory() as session:
        project = await _project(session)
        state = await dream.claim(session, project.id, now=_NOW)
        assert state is not None
        await session.commit()
        project_id = project.id

    async with business_db_factory() as session:
        state = await dream.state_of(session, project_id)
        assert state is not None
        await dream.finish(session, state, now=_NOW + timedelta(hours=1))
        await session.commit()

    async with business_db_factory() as session:
        state = await dream.state_of(session, project_id)
        assert state is not None
        assert state.claimed_at is None
        assert state.last_dream_at == _NOW + timedelta(hours=1)
        # 下一个窗口从这个时刻算起。
        assert dream.since_of(state) == _NOW + timedelta(hours=1)


async def test_a_dream_that_could_not_run_does_not_count_as_one(
    business_db_factory,
):
    """没跑成的那一次不推进 `last_dream_at`：它不算整理过，下一轮该再来。"""
    async with business_db_factory() as session:
        project = await _project(session)
        state = await dream.claim(session, project.id, now=_NOW)
        assert state is not None
        await dream.release(session, state)
        await session.commit()
        project_id = project.id

    async with business_db_factory() as session:
        state = await dream.state_of(session, project_id)
        assert state is not None
        assert state.claimed_at is None
        assert state.last_dream_at is None
        assert dream.since_of(state) == dream.EPOCH


async def test_a_refused_dream_still_moves_the_origin(business_db_factory):
    """拒绝执行的那一次照样推进原点——否则下一次巡检立刻再跑一遍。

    一个坏掉的树会让这个项目一遍遍重复同一场拒绝，而每一次都是一轮真会话。
    """
    async with business_db_factory() as session:
        project = await _project(session)
        state = await dream.claim(session, project.id, now=_NOW)
        assert state is not None
        await dream.finish(session, state, now=_NOW)
        run = await dream.open_run(session, project.id, tokens_at_start=1_000, now=_NOW)
        await dream.close_run(
            session,
            run,
            status=MemoryDreamRunStatus.refused,
            summary="拒绝执行：team 要删 4 条",
            files=[],
            now=_NOW,
        )
        await session.commit()
        project_id = project.id
        run_id = run.id

    async with business_db_factory() as session:
        state = await dream.state_of(session, project_id)
        assert state is not None and state.last_dream_at == _NOW
        stored = await dream.run_of(session, run_id)
        assert stored is not None
        assert stored.status == MemoryDreamRunStatus.refused.value
        assert stored.files_changed == []
        assert "team" in stored.summary


# --- 谁有 private 记忆 ------------------------------------------------------


async def test_private_owners_lists_everyone_with_a_private_tree(
    business_db_factory,
):
    """整理要看的是这个项目**全部**的 private，不是「这一轮在场的几个人」。

    漏掉一个没说过话的人，等于他的记忆没有人整理——而这件事不会有任何迹象。
    """
    from app.domain.memory.files_store import MemoryFileStore

    async with business_db_factory() as session:
        project = await _project(session)
        other = await _project(session, "bob")
        store = MemoryFileStore(session)
        for owner in ("alice", "bob"):
            await store.write(
                project_id=project.id,
                scope=MemoryFileScope.private,
                owner_handle=owner,
                path="MEMORY.md",
                content="- 一条",
                updated_by=owner,
                expected_version=None,
            )
        await store.write(
            project_id=other.id,
            scope=MemoryFileScope.private,
            owner_handle="carol",
            path="MEMORY.md",
            content="- 别人的项目",
            updated_by="carol",
            expected_version=None,
        )
        await session.commit()

        assert await private_owners(session, project.id) == ["alice", "bob"]
        assert await private_owners(session, other.id) == ["carol"]
        assert await private_owners(session, uuid.uuid4()) == []
