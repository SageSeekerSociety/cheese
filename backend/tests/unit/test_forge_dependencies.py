"""Dependency outcomes survive restarts and ask the executor to restack."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select

from app.domain.block.models import Block
from app.domain.project.models import Project
from app.domain.review import pr_poll
from app.domain.review.models import AcceptApproval, AcceptCard, AcceptStatus
from app.domain.review.pr_publish import retarget_completed_dependencies
from app.domain.review.services import AcceptService
from app.domain.room_task.models import Task, TaskStatus
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
async def test_detached_parent_preserves_ancestor_review_for_descendants(
    db_factory, monkeypatch
):
    parent, child = await seed(db_factory, delivered=False)
    reason = "Do not add the obsolete API; keep the independent report."
    async with db_factory() as session:
        rejected = AcceptCard(
            topic_id=parent.room_id,
            task_id=parent.id,
            reviewer_handle="reviewer",
            status=AcceptStatus.rejected,
            note=reason,
        )
        session.add(rejected)
        grandchild = Task(
            project_id=child.project_id,
            room_id=child.room_id,
            title="Report",
            branch_name="report",
            base_branch=child.branch_name,
            base_task_id=child.id,
        )
        session.add(grandchild)
        await session.flush()
        descendant = Task(
            project_id=child.project_id,
            room_id=child.room_id,
            title="Publish report",
            branch_name="publish",
            base_branch=grandchild.branch_name,
            base_task_id=grandchild.id,
        )
        session.add(descendant)
        await session.commit()
    monkeypatch.setattr(AcceptService, "_app_pr_client", AsyncMock())
    await retarget_completed_dependencies(db_factory)
    for completed, notified in ((child, grandchild), (grandchild, descendant)):
        async with db_factory() as session:
            saved = await session.get(Task, completed.id)
            saved.base_task_id = None
            saved.base_branch = "main"
            saved.status = TaskStatus.closed
            saved.closed_at = datetime.now(UTC)
            saved.delivered_head = "b" * 40
            await session.commit()
        await retarget_completed_dependencies(db_factory)
        await retarget_completed_dependencies(db_factory)
        async with db_factory() as session:
            notices = list(
                await session.scalars(
                    select(Block).where(
                        Block.meta["dependency_task_id"].as_string() == str(notified.id)
                    )
                )
            )
            assert len(notices) == 1
            meta = notices[0].meta
            assert meta["dependency_rejection"] is None
            assert len(meta["dependency_review_history"]) == 1
            assert meta["dependency_review_history"][0]["card_id"] == str(rejected.id)
            assert reason in meta["agent_notice"]
            assert "这不是当前状态" in meta["agent_notice"]


@pytest.mark.anyio
@pytest.mark.parametrize("parent_open", [True, False])
@pytest.mark.parametrize("latest_status", [AcceptStatus.rejected, AcceptStatus.pending])
@pytest.mark.parametrize("reason", ["", "保留原接口。\n不要引入新的外部依赖。"])
async def test_dependency_notice_preserves_only_current_rejection(
    db_factory, latest_status, reason, parent_open
):
    parent, _ = await seed(db_factory, delivered=False)
    async with db_factory() as session:
        if parent_open:
            saved_parent = await session.get(Task, parent.id)
            saved_parent.status = TaskStatus.open
            saved_parent.closed_at = None
        session.add(
            AcceptCard(
                topic_id=parent.room_id,
                task_id=parent.id,
                reviewer_handle="reviewer",
                status=AcceptStatus.rejected,
                note="Superseded review",
                created_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        current = AcceptCard(
            topic_id=parent.room_id,
            task_id=parent.id,
            reviewer_handle="reviewer",
            status=latest_status,
            note=reason,
            decided_by="reviewer" if latest_status == AcceptStatus.rejected else None,
        )
        session.add(current)
        await session.commit()
    await retarget_completed_dependencies(db_factory)
    await retarget_completed_dependencies(db_factory)
    async with db_factory() as session:
        blocks = list(await session.scalars(select(Block)))
        if parent_open and latest_status == AcceptStatus.pending:
            assert blocks == []
            return
        assert len(blocks) == 1
        meta = blocks[0].meta
        assert meta["event_type"] == (
            "dependency_rejected" if parent_open else "dependency_closed"
        )
        assert "Superseded review" not in meta["agent_notice"]
        if latest_status == AcceptStatus.rejected:
            assert meta["dependency_rejection"] == {
                "card_id": str(current.id),
                "decided_by": "reviewer",
                "reason": reason,
            }
            assert (reason or "没有填写驳回理由") in meta["agent_notice"]
        else:
            assert meta["dependency_rejection"] is None


@pytest.mark.anyio
async def test_rejected_dependency_can_later_deliver(db_factory, monkeypatch):
    parent, child = await seed(db_factory, delivered=False)
    async with db_factory() as session:
        saved_parent = await session.get(Task, parent.id)
        saved_parent.status = TaskStatus.open
        saved_parent.closed_at = None
        session.add(
            AcceptCard(
                topic_id=parent.room_id,
                task_id=parent.id,
                reviewer_handle="reviewer",
                status=AcceptStatus.rejected,
                note="Keep the existing interface",
            )
        )
        child_card = AcceptCard(
            topic_id=child.room_id,
            task_id=child.id,
            reviewer_handle="reviewer",
            auto_merge_armed_by="reviewer",
            auto_merge_armed_at=datetime.now(UTC),
        )
        session.add(child_card)
        await session.flush()
        session.add(AcceptApproval(card_id=child_card.id, approver_handle="reviewer"))
        await session.commit()
    forge = AsyncMock()
    monkeypatch.setattr(AcceptService, "_app_pr_client", AsyncMock(return_value=forge))
    await retarget_completed_dependencies(db_factory)
    await retarget_completed_dependencies(db_factory)
    forge.update_pr.assert_not_awaited()
    async with db_factory() as session:
        saved_child = await session.get(Task, child.id)
        assert saved_child.status == TaskStatus.open
        assert saved_child.base_branch == "parent"
        assert list(await session.scalars(select(AcceptApproval))) == []
        assert (
            await session.get(AcceptCard, child_card.id)
        ).auto_merge_armed_by is None
        saved_parent = await session.get(Task, parent.id)
        saved_parent.status = TaskStatus.closed
        saved_parent.closed_at = datetime.now(UTC)
        saved_parent.delivered_head = "a" * 40
        await session.commit()
    await retarget_completed_dependencies(db_factory)
    await retarget_completed_dependencies(db_factory)
    forge.update_pr.assert_awaited_once_with(2, base="main")
    async with db_factory() as session:
        blocks = list(await session.scalars(select(Block).order_by(Block.created_at)))
        assert [block.meta["event_type"] for block in blocks] == [
            "dependency_rejected",
            "dependency_closed",
        ]
        assert blocks[1].meta["dependency_rejection"] is None


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


async def assign_parent(factory, task_id):
    from app.domain.agent_instance.models import AgentInstance
    from app.domain.agent_instance.services import AgentInstanceService
    from app.domain.identity.handles import agent_instance_handle
    from app.domain.topic.models import TopicMembership, TopicRole

    async with factory() as session:
        task = await session.get(Task, task_id)
        agent = AgentInstance(
            project_id=task.project_id, handle="executor", configuration={}
        )
        session.add(agent)
        await session.flush()
        await AgentInstanceService(session).ensure_identity(agent)
        session.add(
            TopicMembership(
                topic_id=task.room_id,
                member_handle=agent_instance_handle(agent.id),
                role=TopicRole.member,
            )
        )
        task.execution_agent_instance_id = agent.id
        task.execution_parent_session_id = "native-parent"
        task.execution_turn_id = uuid.uuid4()
        task.subagent_id = "child-worker"
        await session.commit()


@pytest.mark.anyio
async def test_dependency_notice_waits_for_parent_and_recovers_only_unsent_claim(
    db_factory, monkeypatch
):
    from app.domain.delivery.models import Delivery

    parent, child = await seed(db_factory, delivered=False)
    await retarget_completed_dependencies(db_factory)
    chat = SimpleNamespace(session_factory=db_factory)
    runner = Mock()
    monkeypatch.setattr("app.api.deps.get_work_runner", lambda: runner)
    await pr_poll.deliver_dependency_notices(chat)
    runner.submit.assert_not_called()
    await assign_parent(db_factory, child.id)
    async with db_factory() as session:
        row = await session.scalar(select(Delivery))
        row.retry_at = None
        await session.commit()
    await pr_poll.deliver_dependency_notices(chat)
    assert runner.submit.call_count == 1
    first_attempt = runner.submit.call_args.kwargs["turn_id"]
    await pr_poll.deliver_dependency_notices(chat)
    assert runner.submit.call_count == 1
    async with db_factory() as session:
        row = await session.scalar(select(Delivery))
        row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    await pr_poll.deliver_dependency_notices(
        SimpleNamespace(session_factory=db_factory)
    )
    assert runner.submit.call_count == 2
    assert runner.submit.call_args.kwargs["turn_id"] != first_attempt
    assert "native child=child-worker" in runner.submit.call_args.kwargs["content"]


@pytest.mark.anyio
async def test_dependency_delivery_needs_the_matching_native_receipt(
    db_factory, monkeypatch
):
    from app.domain.agent.chat import ChatService
    from app.domain.delivery.agent import begin_send
    from app.domain.delivery.models import Delivery
    from tests.conftest import stub_compute

    parent, child = await seed(db_factory, delivered=False)
    await assign_parent(db_factory, child.id)
    await retarget_completed_dependencies(db_factory)
    chat = ChatService(
        session_factory=db_factory,
        base_system_prompt="Synthetic agent",
        workspace_root="/unused-dependency-receipt-test",
        compute=stub_compute(),
    )
    runner = Mock()
    monkeypatch.setattr("app.api.deps.get_work_runner", lambda: runner)
    await pr_poll.deliver_dependency_notices(chat)
    attempt = runner.submit.call_args.kwargs
    async with db_factory() as session:
        task = await session.get(Task, child.id)
        # Same native parent and child, observed again on a later turn.
        task.execution_turn_id = uuid.uuid4()
        await session.commit()
    await begin_send(
        db_factory,
        attempt["delivery_id"],
        attempt["turn_id"],
        parent_session_id="native-parent",
    )
    chat._pending_receipts[child.room_id] = [
        (attempt["content"], [], attempt["turn_id"], datetime.now(UTC))
    ]
    await chat.confirm_prompt_receipt(child.room_id, "unrelated")
    async with db_factory() as session:
        assert (await session.get(Delivery, attempt["delivery_id"])).state == "sending"
    await chat.confirm_prompt_receipt(child.room_id, attempt["content"])
    async with db_factory() as session:
        row = await session.get(Delivery, attempt["delivery_id"])
        assert row.state == "received" and row.sent_at is not None
    await pr_poll.deliver_dependency_notices(chat)
    assert runner.submit.call_count == 1
