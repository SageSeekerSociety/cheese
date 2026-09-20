"""Dependency outcomes survive restarts and ask the executor to restack."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select

from app.domain.block.models import Block
from app.domain.project.models import Project
from app.domain.review.models import AcceptApproval, AcceptCard
from app.domain.review.pr_publish import retarget_completed_dependencies
from app.domain.review.services import AcceptService
from app.domain.room_task.models import Task, TaskStatus
from app.domain.scheduler.service import SchedulerService
from app.domain.topic.models import Topic


async def seed(factory, *, delivered):
    async with factory() as session:
        project = Project(name="Dependency test")
        session.add(project)
        await session.flush()
        room = Topic(project_id=project.id, title="Room")
        session.add(room)
        await session.flush()
        parent = Task(
            project_id=project.id,
            room_id=room.id,
            title="Parent",
            status=TaskStatus.closed,
            branch_name="parent",
            base_branch="main",
            closed_at=datetime.now(UTC),
            delivered_head="a" * 40 if delivered else None,
        )
        session.add(parent)
        await session.flush()
        child = Task(
            project_id=project.id,
            room_id=room.id,
            title="Child",
            status=TaskStatus.open,
            branch_name="child",
            base_branch="parent",
            base_task_id=parent.id,
            pr_number=2,
        )
        session.add(child)
        await session.commit()
        return parent, child


@pytest.mark.anyio
@pytest.mark.parametrize("delivered", [True, False])
async def test_dependency_notice_is_durable_and_deduplicated(
    db_factory, monkeypatch, delivered
):
    parent, child = await seed(db_factory, delivered=delivered)
    async with db_factory() as session:
        card = AcceptCard(
            topic_id=child.room_id,
            task_id=child.id,
            reviewer_handle="reviewer",
            auto_merge_armed_by="reviewer",
            auto_merge_armed_at=datetime.now(UTC),
        )
        session.add(card)
        await session.flush()
        session.add(AcceptApproval(card_id=card.id, approver_handle="reviewer"))
        await session.commit()
    forge = AsyncMock()
    monkeypatch.setattr(AcceptService, "_app_pr_client", AsyncMock(return_value=forge))
    await retarget_completed_dependencies(db_factory)
    await retarget_completed_dependencies(db_factory)
    async with db_factory() as session:
        saved = await session.get(Task, child.id)
        blocks = list(await session.scalars(select(Block)))
        assert saved.status == TaskStatus.open
        assert saved.base_branch == ("main" if delivered else "parent")
        assert len(blocks) == 1
        notice = blocks[0].meta["agent_notice"]
        assert str(parent.id) in notice and str(child.id) in notice
        assert ("squash" if delivered else "未交付") in notice
        assert blocks[0].meta.get("consumed_turn") is None
        saved_card = await session.get(AcceptCard, card.id)
        assert saved_card.auto_merge_armed_by is None
        assert saved_card.auto_merge_armed_at is None
        assert list(await session.scalars(select(AcceptApproval))) == []
    assert forge.update_pr.await_count == int(delivered)


@pytest.mark.anyio
async def test_failed_retarget_retries_before_announcing(db_factory, monkeypatch):
    await seed(db_factory, delivered=True)
    forge = AsyncMock()
    forge.update_pr.side_effect = [RuntimeError("offline"), {}]
    monkeypatch.setattr(AcceptService, "_app_pr_client", AsyncMock(return_value=forge))
    await retarget_completed_dependencies(db_factory)
    async with db_factory() as session:
        assert list(await session.scalars(select(Block))) == []
    await retarget_completed_dependencies(db_factory)
    async with db_factory() as session:
        assert len(list(await session.scalars(select(Block)))) == 1


@pytest.mark.anyio
async def test_notification_failure_rolls_back_the_dedup_key(db_factory, monkeypatch):
    from app.domain.agent.announce import announce

    await seed(db_factory, delivered=False)
    failing = AsyncMock(side_effect=RuntimeError("write failed"))
    monkeypatch.setattr("app.domain.agent.announce.announce", failing)
    await retarget_completed_dependencies(db_factory)
    monkeypatch.setattr("app.domain.agent.announce.announce", announce)
    await retarget_completed_dependencies(db_factory)
    async with db_factory() as session:
        assert len(list(await session.scalars(select(Block)))) == 1


@pytest.mark.anyio
async def test_concurrent_scans_persist_one_notice(db_factory):
    import asyncio

    await seed(db_factory, delivered=False)
    await asyncio.gather(
        retarget_completed_dependencies(db_factory),
        retarget_completed_dependencies(db_factory),
    )
    async with db_factory() as session:
        assert len(list(await session.scalars(select(Block)))) == 1


@pytest.mark.anyio
async def test_pending_notice_wakes_again_after_restart_until_receipted(
    db_factory, monkeypatch
):
    _, child = await seed(db_factory, delivered=False)
    await retarget_completed_dependencies(db_factory)
    chat = SimpleNamespace(
        session_factory=db_factory,
        has_running_turn=Mock(return_value=False),
        notify_running_turn=AsyncMock(),
    )
    runner = Mock()
    monkeypatch.setattr("app.api.deps.get_work_runner", lambda: runner)
    scheduler = SchedulerService(chat_service=chat)
    await scheduler.deliver_dependency_notices()
    await scheduler.deliver_dependency_notices()
    assert runner.submit.call_count == 1
    assert runner.submit.call_args.args[1] == child.room_id
    assert runner.submit.call_args.kwargs["nudge_event"]
    scheduler = SchedulerService(chat_service=chat)
    await scheduler.deliver_dependency_notices()
    assert runner.submit.call_count == 2
    runner.submit.call_args.kwargs["on_done"]()
    chat.has_running_turn.return_value = True
    await scheduler.deliver_dependency_notices()
    chat.notify_running_turn.assert_awaited_once()
    async with db_factory() as session:
        block = await session.scalar(select(Block))
        block.meta = {**block.meta, "consumed_turn": "acknowledged"}
        await session.commit()
    await scheduler.deliver_dependency_notices()
    assert chat.notify_running_turn.await_count == 1
