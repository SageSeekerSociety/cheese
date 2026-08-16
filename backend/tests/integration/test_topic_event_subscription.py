"""Characterization coverage for the topic event-subscription refactor.

These tests pin the room-visible behavior that must survive moving hook ownership
from one turn to the lifetime of its interactive screen.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest

from app.core.config import settings
from app.domain.agent import event_spool
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.hook_events import HookRouter
from app.domain.agent.hooks_substrate import HooksTurnProvider
from app.domain.agent.runtime import get_broker
from app.domain.agent.service import AgentEvent, AgentResult, AgentService
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.topic.repositories import TopicRepository
from app.domain.topic.services import TopicService
from app.domain.workspace import service as ws

pytestmark = pytest.mark.anyio


class _ImmediateAgent(AgentService):
    def __init__(self) -> None:
        super().__init__(model="stub")

    async def stream_reply(self, **_: object) -> AsyncIterator[AgentEvent]:
        yield AgentResult(text="完成", session_id="session-1", usage=None)


class _AnsweringLiveScreenProvider:
    """A hooks-shaped provider that folds an injected message into its reply."""

    name = "answering-live-screen"
    embeds_images = False

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.injected = asyncio.Event()
        self.delivered: list[str] = []
        self.turns = 0

    def available(self) -> bool:
        return True

    async def run_turn(
        self,
        *,
        prompt: str,
        **_: object,
    ) -> AsyncIterator[AgentEvent]:
        self.turns += 1
        self.started.set()
        await self.injected.wait()
        yield AgentResult(
            text=f"第一条收到：{prompt}\n第二条收到：{self.delivered[-1]}",
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


class _IdleHooksProvider(HooksTurnProvider[str]):
    """A live screen whose hooks can arrive without a platform request."""

    name = "idle-hooks"

    async def _ensure_ready(self, **_: object) -> str:
        return "screen"

    async def _send_prompt(self, screen: str, prompt: str) -> None:
        del screen, prompt


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


async def test_turn_blocks_keep_the_platform_turn_id(client, tmp_path) -> None:
    factory = client.test_factory
    _project_id, topic_id = await _seed_topic(factory)
    service = ChatService(
        session_factory=factory,
        agent=_ImmediateAgent(),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    turn_id = uuid.uuid4()

    await _drain(
        service.converse(
            topic_id=topic_id,
            author="u1",
            content="开始",
            summon=True,
            turn_id=turn_id,
        )
    )

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
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
    assert {block.turn_id for block in conversation} == {turn_id}


async def test_hook_without_a_live_turn_reaches_the_room_from_spool(
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
        "outside-turn-1",
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
    assert frame["block"]["meta"]["eid"] == "outside-turn-1"
    assert "echo outside" in frame["block"]["content"]


async def test_two_messages_during_one_turn_are_both_answered(client, tmp_path) -> None:
    factory = client.test_factory
    _project_id, topic_id = await _seed_topic(factory)
    provider = _AnsweringLiveScreenProvider()
    service = ChatService(
        session_factory=factory,
        agent=_ImmediateAgent(),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider], provider.name),
    )

    first = asyncio.create_task(
        _drain(
            service.converse(
                topic_id=topic_id,
                author="u1",
                content="先处理 A",
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
                content="再处理 B",
                summon=True,
            )
        ),
        1,
    )
    first_frames = await asyncio.wait_for(first, 1)

    assert second_frames[-1]["type"] == "done"
    assert provider.turns == 1
    assert provider.delivered == ["[u2]: 再处理 B"]
    answer = next(
        frame["block"]["content"]
        for frame in first_frames
        if frame["type"] == "assistant_block"
    )
    assert "先处理 A" in answer
    assert "再处理 B" in answer


async def test_session_initiated_turn_is_persisted_and_broadcast(
    client, tmp_path
) -> None:
    factory = client.test_factory
    project_id, topic_id = await _seed_topic(factory)
    router = HookRouter()
    provider = _IdleHooksProvider(router=router)
    ChatService(
        session_factory=factory,
        agent=_ImmediateAgent(),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider], provider.name),
    )
    subscription = await provider.ensure_subscription(project_id, topic_id)
    provider._live[topic_id] = "screen"

    broker = get_broker()
    async with broker.subscribe(str(topic_id)) as room:
        assert router.push(
            str(topic_id),
            {
                "hook_event_name": "SessionStart",
                "session_id": "session-autonomous",
                "_eid": "session-autonomous-1",
            },
        )
        assert router.push(
            str(topic_id),
            {
                "hook_event_name": "MessageDisplay",
                "delta": "后台任务已经完成",
                "_eid": "message-autonomous-1",
            },
        )
        assert router.push(
            str(topic_id),
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "后台任务已经完成",
                "session_id": "session-autonomous",
                "_eid": "stop-autonomous-1",
            },
        )
        message_frame = await asyncio.wait_for(room.get(), 1)
        done_frame = await asyncio.wait_for(room.get(), 1)

    assert message_frame["type"] == "assistant_block"
    assert done_frame == {"type": "done"}
    block = message_frame["block"]
    assert block["turn_id"] is not None
    assert block["meta"] == {
        "eid": "message-autonomous-1",
        "platform_unsolicited": True,
    }

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
        topic = await TopicRepository(session).get(topic_id)
    ai_messages = [
        row
        for row in rows
        if row.kind == BlockKind.message and row.author_type == AuthorType.ai
    ]
    assert [row.content for row in ai_messages] == ["后台任务已经完成"]
    assert topic is not None and topic.session_id == "session-autonomous"

    await provider.drop_subscription(topic_id)
    assert subscription.consumer_task is not None
    assert subscription.consumer_task.done()


async def test_late_hook_opens_a_fresh_unsolicited_turn(client, tmp_path) -> None:
    factory = client.test_factory
    project_id, topic_id = await _seed_topic(factory)
    router = HookRouter()
    topic_key = str(topic_id)

    class _PlatformTurnProvider(_IdleHooksProvider):
        async def _send_prompt(self, screen: str, prompt: str) -> None:
            del screen, prompt
            router.push(
                topic_key,
                {
                    "hook_event_name": "Stop",
                    "last_assistant_message": "平台轮完成",
                    "_eid": "platform-stop-1",
                },
            )

    provider = _PlatformTurnProvider(router=router)
    ChatService(
        session_factory=factory,
        agent=_ImmediateAgent(),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider], provider.name),
    )
    platform_turn_id = uuid.uuid4()
    platform_events = [
        event
        async for event in provider.run_turn(
            project_id=project_id,
            topic_id=topic_id,
            prompt="go",
            system_prompt="",
            resume_session_id=None,
            turn_id=platform_turn_id,
        )
    ]
    assert isinstance(platform_events[-1], AgentResult)

    broker = get_broker()
    async with broker.subscribe(topic_key) as room:
        assert router.push(
            topic_key,
            {
                "hook_event_name": "MessageDisplay",
                "delta": "这是 Stop 后才到的消息",
                "_eid": "late-message-1",
            },
        )
        assert router.push(
            topic_key,
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "这是 Stop 后才到的消息",
                "_eid": "late-stop-1",
            },
        )
        frame = await asyncio.wait_for(room.get(), 1)
        assert await asyncio.wait_for(room.get(), 1) == {"type": "done"}

    assert frame["type"] == "assistant_block"
    assert uuid.UUID(frame["block"]["turn_id"]) != platform_turn_id
    assert frame["block"]["meta"]["platform_unsolicited"] is True
    await provider.drop_subscription(topic_id)
