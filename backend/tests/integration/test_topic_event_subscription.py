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


class _AnsweringLiveScreenProvider(HooksTurnProvider[str]):
    """A hooks-shaped provider that folds an injected message into its reply."""

    name = "answering-live-screen"

    def __init__(self) -> None:
        self.router = HookRouter()
        super().__init__(router=self.router)
        self.started = asyncio.Event()
        self.injected: list[str] = []

    async def _ensure_ready(self, **_: object) -> str:
        return "screen"

    async def _send_prompt(self, screen: str, prompt: str) -> None:
        del screen
        self.injected.append(prompt)
        self.started.set()
        if len(self.injected) < 2:
            return
        answer = "第一条和第二条都收到：先处理 A；再处理 B"
        topic_id = next(iter(self._subscriptions))
        self.router.push(
            str(topic_id),
            {
                "hook_event_name": "MessageDisplay",
                "delta": answer,
                "_eid": "two-messages-answer",
            },
        )
        self.router.push(
            str(topic_id),
            {
                "hook_event_name": "Stop",
                "last_assistant_message": answer,
                "session_id": "session-live",
                "_eid": "two-messages-stop",
            },
        )


class _IdleHooksProvider(HooksTurnProvider[str]):
    """A live screen whose hooks can arrive without a platform request."""

    name = "idle-hooks"

    async def _ensure_ready(self, **_: object) -> str:
        return "screen"

    async def _send_prompt(self, screen: str, prompt: str) -> None:
        del screen, prompt


class _RecoveringHooksProvider(_IdleHooksProvider):
    """A provider that rediscovers one surviving screen after process restart."""

    def __init__(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
        self.project_id = project_id
        self.topic_id = topic_id
        super().__init__(router=HookRouter())

    async def recover_subscriptions(self, device_id: str | None = None):
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

    broker = get_broker()
    async with broker.subscribe(str(topic_id)) as room:
        first_frames = await _drain(
            service.converse(
                topic_id=topic_id,
                author="u1",
                content="先处理 A",
                summon=True,
            )
        )
        await asyncio.wait_for(provider.started.wait(), 1)
        second_frames = await _drain(
            service.converse(
                topic_id=topic_id,
                author="u2",
                content="再处理 B",
                summon=True,
            )
        )
        answer_frame = await asyncio.wait_for(room.get(), 1)
        done_frame = await asyncio.wait_for(room.get(), 1)

    assert first_frames and second_frames
    assert len(provider.injected) == 2
    assert "先处理 A" in provider.injected[0]
    assert "再处理 B" in provider.injected[1]
    assert answer_frame["type"] == "assistant_block"
    assert done_frame == {"type": "done"}
    answer = answer_frame["block"]["content"]
    assert "先处理 A" in answer
    assert "再处理 B" in answer
    await provider.drop_subscription(topic_id)


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


async def test_slow_boot_marker_waits_past_delivery_bound(client, tmp_path) -> None:
    factory = client.test_factory
    project_id, topic_id = await _seed_topic(factory)
    router = HookRouter()

    class _SlowBootProvider(_IdleHooksProvider):
        async def _send_prompt(self, screen: str, prompt: str) -> bool:
            del screen, prompt
            return False

    provider = _SlowBootProvider(
        router=router,
        delivery_timeout_s=0.03,
        idle_suspect_s=0.5,
        hard_ceiling_s=0.5,
    )
    service = ChatService(
        session_factory=factory,
        agent=_ImmediateAgent(),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider], provider.name),
    )
    turn_id = uuid.uuid4()
    broker = get_broker()
    async with broker.subscribe(str(topic_id)) as room:
        frames = await _drain(
            service.converse(
                topic_id=topic_id,
                author="u1",
                content="慢慢启动",
                summon=True,
                turn_id=turn_id,
            )
        )
        assert any(frame["type"] == "event_block" for frame in frames)
        await asyncio.sleep(0.08)  # beyond delivery_timeout_s, below held-prompt bound
        assert room.empty()
        subscription = await provider.ensure_subscription(project_id, topic_id)
        assert subscription.current_turn is not None

        router.push(
            str(topic_id),
            {
                "hook_event_name": "MessageDisplay",
                "delta": "终于启动好了",
                "_eid": "slow-message-1",
            },
        )
        router.push(
            str(topic_id),
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "终于启动好了",
                "_eid": "slow-stop-1",
            },
        )
        answer = await asyncio.wait_for(room.get(), 1)
        assert await asyncio.wait_for(room.get(), 1) == {"type": "done"}

    assert uuid.UUID(answer["block"]["turn_id"]) == turn_id
    assert "platform_unsolicited" not in answer["block"]["meta"]
    await provider.drop_subscription(topic_id)


async def test_marker_timeout_keeps_subscription_for_late_output(
    client, tmp_path
) -> None:
    factory = client.test_factory
    project_id, topic_id = await _seed_topic(factory)
    router = HookRouter()
    provider = _IdleHooksProvider(
        router=router,
        delivery_timeout_s=0.03,
        idle_suspect_s=0.5,
        hard_ceiling_s=0.5,
    )
    service = ChatService(
        session_factory=factory,
        agent=_ImmediateAgent(),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider], provider.name),
    )
    subscription = await provider.ensure_subscription(project_id, topic_id)
    provider._live[topic_id] = "screen"
    # Traffic seen before injection belongs to the unsolicited interval and
    # must not satisfy this platform marker's delivery check.
    router.push(
        str(topic_id),
        {
            "hook_event_name": "SessionStart",
            "session_id": "before-platform-marker",
            "_eid": "before-marker-1",
        },
    )
    await asyncio.sleep(0)
    platform_turn_id = uuid.uuid4()

    broker = get_broker()
    async with broker.subscribe(str(topic_id)) as room:
        await _drain(
            service.converse(
                topic_id=topic_id,
                author="u1",
                content="没有回执",
                summon=True,
                turn_id=platform_turn_id,
            )
        )
        assert (await asyncio.wait_for(room.get(), 1))["type"] == "event_block"
        assert (await asyncio.wait_for(room.get(), 1))["type"] == "error"
        assert await asyncio.wait_for(room.get(), 1) == {"type": "done"}

        assert subscription.current_turn is None
        assert subscription.consumer_task is not None
        assert not subscription.consumer_task.done()
        assert router.push(
            str(topic_id),
            {
                "hook_event_name": "MessageDisplay",
                "delta": "超时后仍然到达",
                "_eid": "after-timeout-message",
            },
        )
        assert router.push(
            str(topic_id),
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "超时后仍然到达",
                "_eid": "after-timeout-stop",
            },
        )
        late = await asyncio.wait_for(room.get(), 1)
        assert await asyncio.wait_for(room.get(), 1) == {"type": "done"}

    assert late["block"]["meta"]["platform_unsolicited"] is True
    assert uuid.UUID(late["block"]["turn_id"]) != platform_turn_id
    await provider.drop_subscription(topic_id)


async def test_restart_replays_spool_into_an_unsolicited_turn(
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
            "delta": "重启期间完成了",
        },
    )
    event_spool.append(
        ws.spool_dir(project_id, topic_id),
        "restart-stop-1",
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "重启期间完成了",
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
        message = await asyncio.wait_for(room.get(), 1)
        assert await asyncio.wait_for(room.get(), 1) == {"type": "done"}

    assert message["type"] == "assistant_block"
    assert message["block"]["meta"] == {
        "eid": "restart-message-1",
        "platform_unsolicited": True,
    }
    assert message["block"]["turn_id"] is not None
    assert event_spool.spool_entries(ws.spool_dir(project_id, topic_id)) == []
    await provider.drop_subscription(topic_id)

    # A second restart begins at the latest persisted eid, inclusively. Replaying
    # that cursor is safe (D5), primes the Stop text-dedup state, and the later
    # event still lands in a fresh correctly-attributed interval.
    event_spool.append(
        ws.spool_dir(project_id, topic_id),
        "restart-message-1",
        {
            "hook_event_name": "MessageDisplay",
            "delta": "重启期间完成了",
        },
    )
    event_spool.append(
        ws.spool_dir(project_id, topic_id),
        "restart-message-2",
        {
            "hook_event_name": "MessageDisplay",
            "delta": "恢复后又完成了一步",
        },
    )
    event_spool.append(
        ws.spool_dir(project_id, topic_id),
        "restart-stop-2",
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "恢复后又完成了一步",
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
    async with broker.subscribe(str(topic_id)) as room:
        assert await service.recover_hook_subscriptions() == 1
        recovered_message = await asyncio.wait_for(room.get(), 1)
        assert await asyncio.wait_for(room.get(), 1) == {"type": "done"}

    assert recovered_message["block"]["meta"]["eid"] == "restart-message-2"
    assert recovered_message["block"]["turn_id"] != message["block"]["turn_id"]
    async with factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic_id)
    assert [
        block.meta.get("eid")
        for block in blocks
        if isinstance(block.meta, dict) and block.meta.get("eid")
    ].count("restart-message-1") == 1
    assert event_spool.spool_entries(ws.spool_dir(project_id, topic_id)) == []
    await provider.drop_subscription(topic_id)


async def test_repeated_hook_eid_is_a_persistence_noop(client, tmp_path) -> None:
    factory = client.test_factory
    project_id, topic_id = await _seed_topic(factory)
    provider = _IdleHooksProvider(router=HookRouter())
    ChatService(
        session_factory=factory,
        agent=_ImmediateAgent(),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider], provider.name),
    )
    await provider.ensure_subscription(project_id, topic_id)
    provider._live[topic_id] = "screen"
    payload = {
        "hook_event_name": "MessageDisplay",
        "delta": "只应该出现一次",
        "_eid": "same-message-eid",
    }

    broker = get_broker()
    async with broker.subscribe(str(topic_id)) as room:
        tool = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "echo once"},
            "_eid": "same-tool-eid",
        }
        assert provider._router.push(str(topic_id), dict(tool))
        assert provider._router.push(str(topic_id), dict(tool))
        assert provider._router.push(str(topic_id), dict(payload))
        assert provider._router.push(str(topic_id), dict(payload))
        assert provider._router.push(
            str(topic_id),
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "只应该出现一次",
                "_eid": "same-message-stop",
            },
        )
        assert (await asyncio.wait_for(room.get(), 1))["type"] == "event_block"
        assert (await asyncio.wait_for(room.get(), 1))["type"] == "assistant_block"
        assert await asyncio.wait_for(room.get(), 1) == {"type": "done"}

    async with factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic_id)
    matches = [
        block
        for block in blocks
        if isinstance(block.meta, dict) and block.meta.get("eid") == "same-message-eid"
    ]
    assert len(matches) == 1
    tool_matches = [
        block
        for block in blocks
        if isinstance(block.meta, dict) and block.meta.get("eid") == "same-tool-eid"
    ]
    assert len(tool_matches) == 1
    await provider.drop_subscription(topic_id)
