"""Idle-container reaper: containers of topics with no recent block activity
(or no topic at all) are removed; active topics keep their box."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.domain.agent.chat import ChatService
from app.domain.block.models import AuthorType, Block
from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.scheduler.service import SandboxReaperRunner, SchedulerService
from app.domain.topic.services import TopicService
from app.domain.workspace import service as ws
from tests.conftest import stub_compute


@pytest.mark.anyio
async def test_reap_removes_idle_and_orphan_keeps_active(client, tmp_path, monkeypatch):
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
        # Backdate the idle topic's only block far past the cutoff.
        await session.execute(
            update(Block)
            .where(Block.topic_id == idle.id)
            .values(created_at=datetime.now(UTC) - timedelta(days=30))
        )
        await session.commit()
        active_id, idle_id = active.id, idle.id

    containers = [
        f"cheesex-tmux-{active_id.hex[:12]}",  # active → kept
        f"cheesex-sbx-{idle_id.hex[:12]}",  # idle → reaped
        f"cheesex-tmux-{uuid.uuid4().hex[:12]}",  # orphan (topic gone) → reaped
    ]
    removed: list[str] = []
    monkeypatch.setattr(ws, "list_sandbox_containers", lambda: list(containers))
    monkeypatch.setattr(ws, "remove_container", removed.append)

    reaped = await svc.reap_idle_containers(idle_hours=3)
    assert reaped == 2
    assert containers[0] not in removed
    assert containers[1] in removed and containers[2] in removed


@pytest.mark.anyio
async def test_reap_releases_idle_device_screens_keeps_active(
    client, tmp_path, monkeypatch
):
    """Device counterpart to the container reaper: a screen whose topic went quiet
    past the cutoff is released, while an active topic's screen stays open. Screens
    live in the device hub, not Docker, so the container reaper never saw them."""
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
    called = asyncio.Event()
    screens_called = asyncio.Event()

    async def reap_idle_containers(idle_hours):
        assert idle_hours == 7
        called.set()
        return 0

    async def reap_idle_device_screens(idle_hours):
        assert idle_hours == 7
        screens_called.set()
        return 0

    scheduler.reap_idle_containers = reap_idle_containers
    scheduler.reap_idle_device_screens = reap_idle_device_screens
    runner = SandboxReaperRunner(scheduler, interval_seconds=0.01, idle_hours=7)
    runner.start()
    await asyncio.wait_for(called.wait(), timeout=1)
    await asyncio.wait_for(screens_called.wait(), timeout=1)
    await runner.stop()


@pytest.mark.anyio
async def test_a_room_with_a_busy_task_keeps_its_box(client, tmp_path, monkeypatch):
    """A tmux box is named after a ROOM and hosts every task split out of it, so
    its idleness is the room's AND its tasks'. Judging the room alone destroys a
    box with live work in it the moment the room's own timeline goes quiet — and
    a room whose work has been split out is quiet BY DESIGN, so this is the
    normal case, not an edge one."""
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
        room = await topics.create(project_id=project.id, title="R", created_by="u")
        task = await topics.dispatch_task(place_id=room.id, title="T", created_by="u")
        blocks = BlockRepository(session)
        for t in (room, task):
            await blocks.add(
                project_id=project.id,
                topic_id=t.id,
                author="u",
                author_type=AuthorType.human,
                content="hi",
            )
        # The ROOM has been silent for a month; its task spoke just now.
        await session.execute(
            update(Block)
            .where(Block.topic_id == room.id)
            .values(created_at=datetime.now(UTC) - timedelta(days=30))
        )
        await session.commit()
        room_id = room.id

    box = f"cheesex-tmux-{room_id.hex[:12]}"
    removed: list[str] = []
    monkeypatch.setattr(ws, "list_sandbox_containers", lambda: [box])
    monkeypatch.setattr(ws, "remove_container", removed.append)

    assert await svc.reap_idle_containers(idle_hours=3) == 0
    assert removed == [], "the task's box was reaped out from under it"


@pytest.mark.anyio
async def test_the_room_of_a_task_is_the_room_it_names(client, tmp_path):
    """The DB half of box placement: a task runs in the box of the room it was
    dispatched in, a room runs in its own, and the answer is recorded where the
    sync workspace layer can read it (`docker port` lookups have no session).

    Named for what it now does. It used to climb `topics.parent_id` to find the
    room, and a thread is not in that table — the climb would have found nothing
    and fallen back to "a box of its own", which is one container per piece of
    work instead of one per room, with nothing anywhere saying so.
    """
    from app.core.config import settings
    from app.domain.agent.tmux_provider import TmuxChannel

    factory = client.test_factory
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topics = TopicService(session)
        room = await topics.create(project_id=project.id, title="R", created_by="u")
        task = await topics.dispatch_task(place_id=room.id, title="T", created_by="u")
        await session.commit()
        project_id, room_id, task_id = project.id, room.id, task.id

    provider = TmuxChannel(image="img:test", session_factory=factory)
    old_root = settings.workspace_root
    settings.workspace_root = str(tmp_path / "rooms")
    try:
        assert await provider._room_id(project_id, task_id) == room_id
        assert await provider._room_id(project_id, room_id) == room_id
        # ...and it is now readable without a DB session.
        assert ws.room_for_topic(task_id) == room_id
        # A place of ANOTHER project can never be pulled into this room's box,
        # even if the ids were somehow crossed: the lookup verifies the project.
        assert await provider._room_id(uuid.uuid4(), task_id) == task_id
    finally:
        settings.workspace_root = old_root
