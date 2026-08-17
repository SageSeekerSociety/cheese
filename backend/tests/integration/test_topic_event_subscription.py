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
from app.domain.agent.hook_events import HookRouter
from app.domain.agent.hooks_substrate import HooksTurnProvider, TopicSubscription
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
from app.domain.topic.repositories import TopicRepository
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


class _IdleHooksProvider(HooksTurnProvider[str]):
    """A live screen whose hooks can arrive without a platform request."""

    name = "idle-hooks"

    async def _ensure_ready(self, **_: object) -> str:
        return "screen"

    async def _send_prompt(self, screen: str, prompt: str) -> None:
        del screen, prompt


class _RecoveringHooksProvider(_IdleHooksProvider):
    """A provider that rediscovers one surviving screen after restart."""

    def __init__(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
        self.project_id = project_id
        self.topic_id = topic_id
        super().__init__(router=HookRouter())

    async def recover_subscriptions(
        self, device_id: str | None = None
    ) -> list[TopicSubscription]:
        del device_id
        subscription = await self.ensure_subscription(
            self.project_id, self.topic_id, paused=True
        )
        self._live[self.topic_id] = "surviving-screen"
        return [subscription]


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


async def test_human_summon_uses_message_id_as_work_attribution(
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

    frames = await _drain(
        service.converse(
            topic_id=topic_id,
            author="u1",
            content="Attribute this work to me",
            summon=True,
        )
    )
    user = next(frame["block"] for frame in frames if frame["type"] == "user_block")
    answer = next(
        frame["block"] for frame in frames if frame["type"] == "assistant_block"
    )

    assert user["turn_id"] == user["id"]
    assert answer["turn_id"] == user["id"]


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


async def test_session_initiated_work_is_persisted_and_broadcast(
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
                "delta": "Background work finished",
                "_eid": "message-autonomous-1",
            },
        )
        assert router.push(
            str(topic_id),
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "Background work finished",
                "session_id": "session-autonomous",
                "_eid": "stop-autonomous-1",
            },
        )
        started_frame = await asyncio.wait_for(room.get(), 1)
        message_frame = await asyncio.wait_for(room.get(), 1)
        done_frame = await asyncio.wait_for(room.get(), 1)
        finished_frame = await asyncio.wait_for(room.get(), 1)

    assert started_frame["type"] == "turn_started"
    assert message_frame["type"] == "assistant_block"
    assert done_frame == {"type": "done"}
    assert finished_frame == {
        "type": "turn_finished",
        "turn_id": started_frame["turn_id"],
    }
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
    assert [row.content for row in ai_messages] == ["Background work finished"]
    assert topic is not None and topic.session_id == "session-autonomous"

    await provider.drop_subscription(topic_id)
    assert subscription.consumer_task is not None
    assert subscription.consumer_task.done()


async def test_late_hook_opens_fresh_unsolicited_work(client, tmp_path) -> None:
    factory = client.test_factory
    project_id, topic_id = await _seed_topic(factory)
    router = HookRouter()
    topic_key = str(topic_id)

    class _PlatformProvider(_IdleHooksProvider):
        async def _send_prompt(self, screen: str, prompt: str) -> None:
            del screen, prompt
            router.push(
                topic_key,
                {
                    "hook_event_name": "Stop",
                    "last_assistant_message": "Requested work finished",
                    "_eid": "requested-stop-1",
                },
            )

    provider = _PlatformProvider(router=router)
    ChatService(
        session_factory=factory,
        agent=_ImmediateAgent(),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider], provider.name),
    )
    requested_id = uuid.uuid4()
    events = [
        event
        async for event in provider.run_turn(
            project_id=project_id,
            topic_id=topic_id,
            prompt="go",
            system_prompt="",
            resume_session_id=None,
            turn_id=requested_id,
        )
    ]
    assert isinstance(events[-1], AgentResult)

    broker = get_broker()
    async with broker.subscribe(topic_key) as room:
        assert router.push(
            topic_key,
            {
                "hook_event_name": "MessageDisplay",
                "delta": "Late output",
                "_eid": "late-message-1",
            },
        )
        assert router.push(
            topic_key,
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "Late output",
                "_eid": "late-stop-1",
            },
        )
        started = await asyncio.wait_for(room.get(), 1)
        frame = await asyncio.wait_for(room.get(), 1)
        assert await asyncio.wait_for(room.get(), 1) == {"type": "done"}
        finished = await asyncio.wait_for(room.get(), 1)

    assert started["type"] == "turn_started"
    assert frame["type"] == "assistant_block"
    assert finished == {
        "type": "turn_finished",
        "turn_id": started["turn_id"],
    }
    assert uuid.UUID(frame["block"]["turn_id"]) != requested_id
    assert frame["block"]["meta"]["platform_unsolicited"] is True
    await provider.drop_subscription(topic_id)


async def test_restart_reattaches_and_replays_spooled_hooks(
    client, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    project_id, topic_id = await _seed_topic(factory)
    event_spool.append(
        ws.spool_dir(project_id, topic_id),
        "restart-message-1",
        {
            "hook_event_name": "MessageDisplay",
            "delta": "Finished during restart",
        },
    )
    event_spool.append(
        ws.spool_dir(project_id, topic_id),
        "restart-stop-1",
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "Finished during restart",
            "session_id": "session-after-restart",
        },
    )
    provider = _RecoveringHooksProvider(project_id, topic_id)
    service = ChatService(
        session_factory=factory,
        agent=_ImmediateAgent(),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider], provider.name),
    )

    broker = get_broker()
    async with broker.subscribe(str(topic_id)) as room:
        assert await service.recover_hook_subscriptions() == 1
        started = await asyncio.wait_for(room.get(), 1)
        message = await asyncio.wait_for(room.get(), 1)
        assert await asyncio.wait_for(room.get(), 1) == {"type": "done"}
        finished = await asyncio.wait_for(room.get(), 1)

    assert started["type"] == "turn_started"
    assert message["type"] == "assistant_block"
    assert finished == {
        "type": "turn_finished",
        "turn_id": started["turn_id"],
    }
    assert message["block"]["meta"] == {
        "eid": "restart-message-1",
        "platform_unsolicited": True,
    }
    assert event_spool.spool_entries(ws.spool_dir(project_id, topic_id)) == []
    await provider.drop_subscription(topic_id)


async def test_session_timeout_retires_activity_but_keeps_subscription(
    client, tmp_path
) -> None:
    factory = client.test_factory
    project_id, topic_id = await _seed_topic(factory)
    router = HookRouter()
    provider = _IdleHooksProvider(
        router=router,
        idle_suspect_s=0.2,
        hard_ceiling_s=0.2,
        delivery_timeout_s=0.03,
    )
    service = ChatService(
        session_factory=factory,
        agent=_ImmediateAgent(),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider], provider.name),
    )

    work_id = uuid.uuid4()
    broker = get_broker()
    async with broker.subscribe(str(topic_id)) as room:
        await _drain(
            service.converse(
                topic_id=topic_id,
                author="u1",
                content="Wait for a response",
                summon=True,
                turn_id=work_id,
            )
        )
        timeout_frames = [await asyncio.wait_for(room.get(), 1) for _ in range(5)]

    assert [frame["type"] for frame in timeout_frames] == [
        "turn_started",
        "event_block",
        "error",
        "done",
        "turn_finished",
    ]
    subscription = provider._subscriptions[topic_id]
    assert subscription.activity is None
    assert subscription.current_turn is None
    assert router.subscribe(str(topic_id)) is subscription.sink

    async with broker.subscribe(str(topic_id)) as room:
        assert router.push(
            str(topic_id),
            {
                "hook_event_name": "MessageDisplay",
                "delta": "Late output survived",
                "_eid": "late-after-timeout-message",
            },
        )
        assert router.push(
            str(topic_id),
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "Late output survived",
                "_eid": "late-after-timeout-stop",
            },
        )
        late_frames = [await asyncio.wait_for(room.get(), 1) for _ in range(4)]

    assert [frame["type"] for frame in late_frames] == [
        "turn_started",
        "assistant_block",
        "done",
        "turn_finished",
    ]
    assert late_frames[1]["block"]["meta"]["platform_unsolicited"] is True
    assert late_frames[1]["block"]["turn_id"] != str(work_id)
    await provider.drop_subscription(topic_id)
