"""Idle-screen reaper: the screen of a topic with no recent block activity is
released; an active topic keeps its screen open."""

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
async def test_reap_releases_idle_device_screens_keeps_active(
    client, tmp_path, monkeypatch
):
    """A screen whose topic went quiet past the cutoff is released, while an
    active topic's screen stays open."""
    from app.domain.agent import device_hub as dh
    from app.domain.agent import device_provider as dp
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

    async def fake_release(project_id, topic_id, **kw):
        released.append(topic_id)

    monkeypatch.setattr(dp, "release_topic_screen", fake_release)

    freed = await svc.reap_idle_device_screens(idle_hours=3)
    assert freed == 1
    assert released == [idle_id]  # only the idle topic's screen is freed


@pytest.mark.anyio
async def test_reaper_runs_without_heartbeat_scheduler():
    scheduler = type("Scheduler", (), {})()
    screens_called = asyncio.Event()

    async def reap_idle_device_screens(idle_hours):
        assert idle_hours == 7
        screens_called.set()
        return 0

    scheduler.reap_idle_device_screens = reap_idle_device_screens
    runner = PeriodicRunner(
        "idle screen reap", 0.01, lambda: scheduler.reap_idle_device_screens(7)
    )
    runner.start()
    await asyncio.wait_for(screens_called.wait(), timeout=1)
    await runner.stop()
