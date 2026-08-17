"""Characterization coverage for retiring platform-defined turns."""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.domain.agent import event_spool
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.runtime import get_broker
from app.domain.agent.service import (
    AgentEvent,
    AgentResult,
    AgentService,
    AgentUsage,
)
from app.domain.block.models import AuthorType, BlockKind, consumed_turn
from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from app.domain.usage.models import ResourceUsage
from app.domain.workspace import service as ws

pytestmark = pytest.mark.anyio


class _ImmediateAgent(AgentService):
    def __init__(self) -> None:
        super().__init__(model="stub")

    async def stream_reply(self, **_: object) -> AsyncIterator[AgentEvent]:
        yield AgentResult(
            text="完成",
            session_id="session-1",
            usage=AgentUsage(
                model="stub",
                input_tokens=3,
                output_tokens=2,
                cost_usd=0.01,
            ),
        )


class _AnsweringLiveScreenProvider:
    """A live-screen provider that accepts a message before finishing its run."""

    name = "answering-live-screen"
    embeds_images = False

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.injected = asyncio.Event()
        self.release = asyncio.Event()
        self.delivered: list[str] = []
        self.runs = 0

    def available(self) -> bool:
        return True

    async def run_turn(
        self,
        *,
        prompt: str,
        **_: object,
    ) -> AsyncIterator[AgentEvent]:
        self.runs += 1
        self.started.set()
        await self.injected.wait()
        await self.release.wait()
        yield AgentResult(
            text=f"First: {prompt}\nSecond: {self.delivered[-1]}",
            session_id="session-live",
            usage=None,
        )

    async def deliver(self, topic_id: uuid.UUID, text: str) -> bool:
        del topic_id
        self.delivered.append(text)
        self.injected.set()
        return True

    def checkpoint(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
        del project_id, topic_id


async def _seed_topic(factory: object) -> tuple[uuid.UUID, uuid.UUID]:
    async with factory() as session:  # type: ignore[operator]
        project = await ProjectService(session).create(name="P", owner_handle="u1")
        topic = await TopicService(session).create(
            project_id=project.id,
            title="T",
            created_by="u1",
        )
        await session.commit()
    return project.id, topic.id


async def _drain(frames: AsyncIterator[dict]) -> list[dict]:
    return [frame async for frame in frames]


async def test_exchange_blocks_and_usage_share_the_supplied_id(
    client, tmp_path
) -> None:
    factory = client.test_factory
    _project_id, topic_id = await _seed_topic(factory)
    service = ChatService(
        session_factory=factory,
        agent=_ImmediateAgent(),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    work_id = uuid.uuid4()

    await _drain(
        service.converse(
            topic_id=topic_id,
            author="u1",
            content="Start",
            summon=True,
            turn_id=work_id,
        )
    )

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
        usage_rows = list(
            await session.scalars(
                select(ResourceUsage).where(ResourceUsage.topic_id == topic_id)
            )
        )
    conversation = [
        block
        for block in rows
        if block.kind == BlockKind.message
        and block.author_type in {AuthorType.human, AuthorType.ai}
    ]
    assert [block.author_type for block in conversation] == [
        AuthorType.human,
        AuthorType.ai,
    ]
    assert {block.turn_id for block in conversation} == {work_id}
    assert len(usage_rows) == 1
    assert usage_rows[0].turn_id == work_id


async def test_hook_without_a_live_run_reaches_the_room_from_spool(
    client, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    project_id, topic_id = await _seed_topic(factory)
    service = ChatService(
        session_factory=factory,
        agent=_ImmediateAgent(),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    event_spool.append(
        ws.spool_dir(project_id, topic_id),
        "outside-run-1",
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "echo outside"},
        },
    )

    broker = get_broker()
    async with broker.subscribe(str(topic_id)) as room:
        assert await service.settle_spool(topic_id) == 1
        frame = await asyncio.wait_for(room.get(), 1)

    assert frame["type"] == "event_block"
    assert frame["block"]["meta"]["eid"] == "outside-run-1"
    assert "echo outside" in frame["block"]["content"]


async def test_mid_run_message_is_consumed_before_the_run_succeeds(
    client, tmp_path
) -> None:
    factory = client.test_factory
    _project_id, topic_id = await _seed_topic(factory)
    provider = _AnsweringLiveScreenProvider()
    service = ChatService(
        session_factory=factory,
        agent=_ImmediateAgent(),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider], provider.name),  # type: ignore[list-item]
    )

    first = asyncio.create_task(
        _drain(
            service.converse(
                topic_id=topic_id,
                author="u1",
                content="Handle A",
                summon=True,
            )
        )
    )
    await asyncio.wait_for(provider.started.wait(), 1)
    second_frames = await asyncio.wait_for(
        _drain(
            service.converse(
                topic_id=topic_id,
                author="u2",
                content="Also handle B",
                summon=True,
            )
        ),
        1,
    )

    assert second_frames[-1]["type"] == "done"
    assert not first.done()
    assert provider.runs == 1
    assert provider.delivered == ["[u2]: Also handle B"]
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    delivered = [block for block in rows if block.content == "Also handle B"]
    assert len(delivered) == 1
    assert consumed_turn(delivered[0]) is not None

    provider.release.set()
    first_frames = await asyncio.wait_for(first, 1)
    answer = next(
        frame["block"]["content"]
        for frame in first_frames
        if frame["type"] == "assistant_block"
    )
    assert "Handle A" in answer
    assert "Also handle B" in answer
