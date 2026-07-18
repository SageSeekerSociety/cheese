"""Idle-container reaper: containers of topics with no recent block activity
(or no topic at all) are removed; active topics keep their box."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.domain.agent.chat import ChatService
from app.domain.agent.service import AgentService
from app.domain.block.models import AuthorType, Block
from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.scheduler.service import SchedulerService
from app.domain.topic.services import TopicService
from app.domain.workspace import service as ws


@pytest.mark.anyio
async def test_reap_removes_idle_and_orphan_keeps_active(client, tmp_path, monkeypatch):
    factory = client.test_factory
    chat = ChatService(
        session_factory=factory,
        agent=AgentService(model="stub"),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
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

    reaped = await svc.reap_idle_containers(idle_days=3)
    assert reaped == 2
    assert containers[0] not in removed
    assert containers[1] in removed and containers[2] in removed
