"""Quiet and recently active open rooms both retain their device screens."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.core.background import PeriodicRunner
from app.domain.agent.chat import ChatService
from app.domain.block.models import AuthorType, Block
from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.scheduler.service import SchedulerService
from app.domain.topic.services import TopicService
from tests.conftest import stub_compute


@pytest.mark.anyio
async def test_idle_and_active_rooms_both_keep_screens(client, tmp_path, monkeypatch):
    """A month without activity does not release an open room's screen."""
    from app.domain.agent import device_hub as dh
    from app.domain.agent.device_hub import HubScreen

    factory = client.test_factory
    chat = ChatService(
        session_factory=factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=stub_compute(),
    )
    svc = SchedulerService(chat_service=chat)

    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topics = TopicService(session)
        active = await topics.create(project_id=project.id, title="A", created_by="u")
        idle = await topics.create(project_id=project.id, title="I", created_by="u")
        blocks = BlockRepository(session)
        for t in (active, idle):
            await blocks.add(
                project_id=project.id,
                topic_id=t.id,
                author="u",
                author_type=AuthorType.human,
                content="hi",
            )
        await session.execute(
            update(Block)
            .where(Block.topic_id == idle.id)
            .values(created_at=datetime.now(UTC) - timedelta(days=30))
        )
        await session.commit()
        active_id, idle_id, pid = active.id, idle.id, project.id

    screens = [
        HubScreen(
            sid="s-active",
            device_id="dev1",
            command=[],
            token="a",
            agent_user_id=1,
            agent_handle="cheese",
            project_id=pid,
            topic_id=active_id,
        ),
        HubScreen(
            sid="s-idle",
            device_id="dev1",
            command=[],
            token="b",
            agent_user_id=1,
            agent_handle="cheese",
            project_id=pid,
            topic_id=idle_id,
        ),
    ]
    monkeypatch.setattr(dh.device_hub, "all_online_screens", lambda: list(screens))
    released: list = []

    async def fake_release(device_id, sid):
        released.append(sid)

    monkeypatch.setattr(dh.device_hub, "close_screen", fake_release)

    freed = await svc.consolidate_idle_device_screens(idle_hours=3)
    assert freed == 0
    assert released == []


@pytest.mark.anyio
async def test_reaper_runs_without_heartbeat_scheduler():
    scheduler = type("Scheduler", (), {})()
    screens_called = asyncio.Event()

    async def consolidate_idle_device_screens(idle_hours):
        assert idle_hours == 7
        screens_called.set()
        return 0

    scheduler.consolidate_idle_device_screens = consolidate_idle_device_screens
    runner = PeriodicRunner(
        "idle memory consolidation",
        0.01,
        lambda: scheduler.consolidate_idle_device_screens(7),
    )
    runner.start()
    await asyncio.wait_for(screens_called.wait(), timeout=1)
    await runner.stop()
