"""Characterization coverage for retiring platform-defined turns."""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.harness.claude_code import event_spool
from app.domain.agent.harness.claude_code.hook_events import HookRouter
from app.domain.agent.harness.claude_code.hooks_substrate import (
    Channel,
    ClaudeCodeRuntime,
)
from app.domain.agent.models import AgentTurn
from app.domain.agent.runtime import AgentWorkRunner, get_broker
from app.domain.agent.service import (
    AgentResult,
    AgentSubagentStart,
    AgentSubagentStop,
    AgentToolUse,
)
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.models import AuthorType, BlockKind, consumed_turn
from app.domain.block.repositories import BlockRepository
from app.domain.identity.handles import CHEESE_HANDLE
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from app.domain.usage.models import ResourceUsage
from app.domain.usage.repositories import UsageRepository
from app.domain.workspace import service as ws
from tests.conftest import StubChannel, settle_turn, stub_compute
from tests.turn_log import open_turn

pytestmark = pytest.mark.anyio


class _ImmediateScreen(StubChannel):
    """A session that answers the moment it is spoken to."""

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        del reply
        self.starts(topic_id, session_id="session-1")
        self.acknowledges(topic_id, prompt)
        self.hook(
            topic_id,
            hook_event_name="Stop",
            session_id="session-1",
            last_assistant_message="完成",
            usage={
                "model": "stub",
                "input_tokens": 3,
                "output_tokens": 2,
                "cost_usd": 0.01,
            },
        )


class _AnsweringLiveScreen(StubChannel):
    """A live session that takes a message before it finishes its turn."""

    name = "answering-live-screen"
    embeds_images = False

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.injected = asyncio.Event()
        self.release = asyncio.Event()
        self.delivered: list[str] = []
        self.runs = 0
        self._answering: set[asyncio.Task] = set()

    async def send_prompt(
        self, screen: uuid.UUID, prompt: str, images: list[dict] | None = None
    ) -> bool:
        del images
        if self._answering:
            # A write into a session that is already working — the transport
            # cannot tell it from the one that started the turn, and neither
            # can a real screen. What makes it an injection rather than a
            # second turn is decided above, on the live-screen lookup.
            self.delivered.append(prompt)
            self.injected.set()
            return True
        self.runs += 1
        self.last_prompt = prompt
        self.started.set()
        task = asyncio.get_running_loop().create_task(self._answer(screen, prompt))
        self._answering.add(task)
        task.add_done_callback(self._answering.discard)
        return True

    async def _answer(self, topic_id: uuid.UUID, prompt: str) -> None:
        await self.injected.wait()
        await self.release.wait()
        self.starts(topic_id, session_id="session-live")
        self.acknowledges(topic_id, prompt)
        self.stops(
            topic_id,
            f"First: {prompt}\nSecond: {self.delivered[-1]}",
            session_id="session-live",
        )


class _IdleChannel(Channel):
    """A live screen whose hooks can arrive without a platform request."""

    name = "idle-hooks"

    async def ensure_ready(self, **_: object) -> str:
        return "screen"

    async def send_prompt(self, screen: str, prompt: str) -> None:
        del screen, prompt


class _RecoveringChannel(_IdleChannel):
    """A channel that rediscovers one surviving screen after restart."""

    def __init__(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
        self.project_id = project_id
        self.topic_id = topic_id

    async def discover(
        self, device_id: str | None = None
    ) -> list[tuple[uuid.UUID, uuid.UUID, object | None, str | None]]:
        del device_id
        return [(self.project_id, self.topic_id, "surviving-screen", "claude-code")]


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
        compute=stub_compute(_ImmediateScreen()),
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
    await settle_turn(service, topic_id)

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
        compute=stub_compute(_ImmediateScreen()),
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
    await settle_turn(service, topic_id)

    assert user["turn_id"] == user["id"]
    async with factory() as session:
        answers = [
            block
            for block in await BlockRepository(session).list_for_topic(topic_id)
            if block.author_type == AuthorType.ai and block.kind == BlockKind.message
        ]
    assert [str(block.turn_id) for block in answers] == [user["id"]]
    async with factory() as session:
        usage = (
            await session.scalars(
                select(ResourceUsage).where(ResourceUsage.topic_id == topic_id)
            )
        ).one()
        aggregate = await UsageRepository(session).for_topic(topic_id)
    assert str(usage.turn_id) == user["id"]
    assert aggregate["turns"] == 1


async def test_hook_without_a_live_run_reaches_the_room_from_spool(
    client, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    project_id, topic_id = await _seed_topic(factory)
    service = ChatService(
        session_factory=factory,
        compute=stub_compute(_ImmediateScreen()),
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
    provider = _AnsweringLiveScreen()
    service = ChatService(
        session_factory=factory,
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider.runtime], provider.name),
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
    await asyncio.wait_for(provider.started.wait(), 5)
    second_frames = await asyncio.wait_for(
        _drain(
            service.converse(
                topic_id=topic_id,
                author="u2",
                content="Also handle B",
                summon=True,
            )
        ),
        2,
    )

    assert all(frame["type"] != "done" for frame in second_frames)
    # The first turn is still OPEN — the session has not stopped — even though
    # the call that started it returned long ago. That gap is the point of the
    # contract: feeding and reading are separate, so "still working" is a fact
    # about the session, never about whether a caller is still holding on.
    assert any(t == topic_id for t, _ in service._hook_work)
    assert provider.runs == 1
    assert provider.delivered == ["[u2]: Also handle B"]
    # #539 decision A: the write-accept delivered it, but the consumed stamp
    # waits for the session's UserPromptSubmit receipt — until then the
    # message stays pending so a session death replays it.
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    delivered = [block for block in rows if block.content == "Also handle B"]
    assert len(delivered) == 1
    assert consumed_turn(delivered[0]) is None
    await service.confirm_prompt_receipt(topic_id, provider.delivered[0])
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    delivered = [block for block in rows if block.content == "Also handle B"]
    assert consumed_turn(delivered[0]) is not None

    provider.release.set()
    await asyncio.wait_for(first, 5)
    await settle_turn(service, topic_id)
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    answer = next(block.content for block in rows if block.author_type == AuthorType.ai)
    # One answer, covering both messages: the second reached the session that
    # was already working, rather than queueing behind the turn.
    assert "Handle A" in answer
    assert "Also handle B" in answer


async def test_session_initiated_work_is_persisted_and_broadcast(
    client, tmp_path
) -> None:
    factory = client.test_factory
    project_id, topic_id = await _seed_topic(factory)
    router = HookRouter()
    provider = ClaudeCodeRuntime(_IdleChannel(), router=router)
    ChatService(
        session_factory=factory,
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
        resumes_by = await AgentSessionService(session).resume_token(
            topic_id, CHEESE_HANDLE
        )
    ai_messages = [
        row
        for row in rows
        if row.kind == BlockKind.message and row.author_type == AuthorType.ai
    ]
    assert [row.content for row in ai_messages] == ["Background work finished"]
    assert resumes_by == "session-autonomous"

    await provider._close_topic(topic_id)
    assert subscription.consumer_task is not None
    assert subscription.consumer_task.done()


async def test_a_subagents_boundaries_pass_through_the_room_untouched(
    client, tmp_path
) -> None:
    """一个会话里同时有几个工人干活时，房间该看到的东西一点没变。

    分身的起止是给平台看的归属信息，不是房间里的一条动静：它们不落库、不广播、
    也不点亮「正在处理」。会话自己说的话、分身发出的工具调用照旧落地——分身的
    工具钩子本来就一直混在这条流里，只是从今天起带上了它是谁。
    """
    factory = client.test_factory
    project_id, topic_id = await _seed_topic(factory)
    router = HookRouter()
    provider = ClaudeCodeRuntime(_IdleChannel(), router=router)
    ChatService(
        session_factory=factory,
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider], provider.name),
    )
    await provider.ensure_subscription(project_id, topic_id)
    provider._live[topic_id] = "screen"

    # Watch what the REAL consumer is handed, not just what the room ends up
    # showing: "nothing was published" is also what a hook nobody translated
    # looks like, and those two have to be told apart.
    handed: list[object] = []
    consumer = provider._event_consumer
    assert consumer is not None

    async def watching(*args):
        handed.append(args[3])
        return await consumer(*args)

    provider.bind_events(watching)

    broker = get_broker()
    async with broker.subscribe(str(topic_id)) as room:
        assert router.push(
            str(topic_id),
            {
                "hook_event_name": "SubagentStart",
                "agent_id": "worker-1",
                "agent_type": "general-purpose",
                "session_id": "session-subagent",
                "_eid": "subagent-start-1",
            },
        )
        assert router.push(
            str(topic_id),
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "rg TODO"},
                "agent_id": "worker-1",
                "agent_type": "general-purpose",
                "_eid": "subagent-tool-1",
            },
        )
        assert router.push(
            str(topic_id),
            {
                "hook_event_name": "SubagentStop",
                "agent_id": "worker-1",
                "agent_type": "general-purpose",
                "last_assistant_message": "分身查完了",
                "agent_transcript_path": "/home/u/.claude/projects/w/sub.jsonl",
                "_eid": "subagent-stop-1",
            },
        )
        assert router.push(
            str(topic_id),
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "会话答完了",
                "session_id": "session-subagent",
                "_eid": "stop-subagent-1",
            },
        )
        frames = [await asyncio.wait_for(room.get(), 1) for _ in range(5)]

    kinds = [frame["type"] for frame in frames]
    assert kinds == [
        "turn_started",
        "event_block",
        "assistant_block",
        "done",
        "turn_finished",
    ]
    assert frames[1]["block"]["meta"]["eid"] == "subagent-tool-1"
    assert frames[2]["block"]["content"] == "会话答完了"

    started = [e for e in handed if isinstance(e, AgentSubagentStart)]
    stopped = [e for e in handed if isinstance(e, AgentSubagentStop)]
    assert [(e.agent_id, e.agent_type) for e in started] == [
        ("worker-1", "general-purpose")
    ]
    assert [(e.agent_id, e.text) for e in stopped] == [("worker-1", "分身查完了")]
    # 那条工具调用是谁发的，事件上说得出来——T2 要按这个把活归到卡上。
    tool = next(e for e in handed if isinstance(e, AgentToolUse))
    assert (tool.agent_id, tool.agent_type) == ("worker-1", "general-purpose")

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    # 分身的收尾话没有变成第二条 AI 发言：它今天只是被认出来，还没有归属可写。
    assert [
        row.content
        for row in rows
        if row.kind == BlockKind.message and row.author_type == AuthorType.ai
    ] == ["会话答完了"]
    assert "分身查完了" not in [row.content for row in rows]

    await provider._close_topic(topic_id)


async def test_late_hook_opens_fresh_unsolicited_work(client, tmp_path) -> None:
    factory = client.test_factory
    project_id, topic_id = await _seed_topic(factory)
    router = HookRouter()
    topic_key = str(topic_id)

    class _PlatformChannel(_IdleChannel):
        async def send_prompt(self, screen: str, prompt: str) -> None:
            del screen, prompt
            router.push(
                topic_key,
                {
                    "hook_event_name": "Stop",
                    "last_assistant_message": "Requested work finished",
                    "_eid": "requested-stop-1",
                },
            )

    provider = ClaudeCodeRuntime(_PlatformChannel(), router=router)
    ChatService(
        session_factory=factory,
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
    await provider._close_topic(topic_id)


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
    provider = ClaudeCodeRuntime(
        _RecoveringChannel(project_id, topic_id), router=HookRouter()
    )
    service = ChatService(
        session_factory=factory,
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider], provider.name),
    )

    broker = get_broker()
    async with broker.subscribe(str(topic_id)) as room:
        assert await service.recover_sessions() == 1
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
    # Replayed to the end. The files stay for their retention window; what says
    # they were consumed is the cursor, so the tail past it must be empty.
    spool = ws.spool_dir(project_id, topic_id)
    assert event_spool.spool_entries(spool, after=event_spool.read_cursor(spool)) == []
    await provider._close_topic(topic_id)


async def test_a_deploy_does_not_interrupt_a_turn_that_is_already_running(
    client, tmp_path, monkeypatch
) -> None:
    """#316 / #459, as a person would check it: restart the backend mid-turn and
    the turn finishes anyway — nothing re-prompted, nothing announced, every
    event landing exactly once.

    What survives a deploy is the screen, not the coroutine waiting on it. So
    this drives the real shape of a restart: an interval left open by a process
    that is gone, hook events waiting in the spool, and a provider that
    rediscovers the screen on the way up. The turn is adopted rather than swept,
    and what ends it is the Stop that screen sends — the interval closes without
    anyone deciding it should.
    """
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    project_id, topic_id = await _seed_topic(factory)

    # The dead process got as far as handing the prompt to the transport.
    interrupted = await open_turn(
        factory, topic_id, content="把测试跑绿", age_s=300, delivered=True
    )
    for eid, payload in (
        (
            "deploy-msg-1",
            {"hook_event_name": "MessageDisplay", "delta": "跑绿了，收工"},
        ),
        (
            "deploy-stop-1",
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "跑绿了，收工",
                "session_id": "session-across-the-deploy",
            },
        ),
    ):
        event_spool.append(ws.spool_dir(project_id, topic_id), eid, payload)

    provider = ClaudeCodeRuntime(
        _RecoveringChannel(project_id, topic_id), router=HookRouter()
    )
    service = ChatService(
        session_factory=factory,
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider], provider.name),
    )
    assert await service.recover_sessions() == 1

    # The startup sweep runs next, exactly as `main.py` orders it. It must find
    # nothing to do: the screen answered for this topic and the prompt reached
    # it.
    runner = AgentWorkRunner(get_broker())
    assert await runner.resume_orphans(service) == 0
    async with factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic_id)
    assert [b.content for b in blocks if b.author_type == AuthorType.ai] == [
        "跑绿了，收工"
    ]
    # Nothing was announced — from the room's side the deploy did not happen.
    assert [b for b in blocks if b.author_type == AuthorType.system] == []
    # And the Stop closed the books on the interval the dead process opened.
    async with factory() as session:
        row = await session.get(AgentTurn, interrupted)
    assert row is not None and row.stopped_at is not None
    await provider._close_topic(topic_id)


async def test_a_stop_does_not_end_a_turn_that_was_never_fed(
    client, tmp_path, monkeypatch
) -> None:
    """A turn spends its first seconds — or minutes, if the box has to boot —
    between opening its interval and reaching the transport. A Stop from the
    conversation BEFORE it must not close that interval.

    An interval closed early is a running turn no sweep can see, which is the
    silent death the whole table exists to end; it would arrive through the one
    door left open, a stale hook. So the rule is the interval's own definition:
    投喂 → Stop, and a turn nobody fed is not what this Stop is ending.
    """
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    project_id, topic_id = await _seed_topic(factory)
    still_provisioning = await open_turn(
        factory, topic_id, content="新任务", age_s=5, delivered=False
    )
    event_spool.append(
        ws.spool_dir(project_id, topic_id),
        "stale-stop-1",
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "上一段对话的收尾",
            "session_id": "session-before",
        },
    )

    provider = ClaudeCodeRuntime(
        _RecoveringChannel(project_id, topic_id), router=HookRouter()
    )
    service = ChatService(
        session_factory=factory,
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider], provider.name),
    )
    assert await service.recover_sessions() == 1

    async with factory() as session:
        row = await session.get(AgentTurn, still_provisioning)
    assert row is not None and row.stopped_at is None
    await provider._close_topic(topic_id)


async def test_an_accepted_write_is_announced_to_the_runtime(client, tmp_path) -> None:
    """`converse` must emit `prompt_delivered` once the transport took the write.

    The runtime stamps the durable in-flight registry on that frame, which is
    what lets a restart tell "the prompt never arrived" from "it arrived and
    芝士 had not produced anything yet". Nothing else observes the frame, so
    without this test it can stop being emitted with no visible symptom — until
    a deploy re-sends a prompt 芝士 already has.

    A refused write raises out of `send` instead, so reaching this frame
    is itself the acceptance (#563)."""
    factory = client.test_factory
    project_id, topic_id = await _seed_topic(factory)
    del project_id
    provider = ClaudeCodeRuntime(_IdleChannel(), router=HookRouter())
    service = ChatService(
        session_factory=factory,
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider], provider.name),
    )

    frames = await _drain(
        service.converse(
            topic_id=topic_id,
            author="u1",
            content="Do the thing",
            summon=True,
        )
    )
    assert "prompt_delivered" in [frame["type"] for frame in frames]
    await provider._close_topic(topic_id)


async def test_session_timeout_retires_activity_but_keeps_subscription(
    client, tmp_path
) -> None:
    factory = client.test_factory
    project_id, topic_id = await _seed_topic(factory)
    router = HookRouter()

    class RecoveringChannel(_IdleChannel):
        alive = False

        async def confirm_alive(self, screen):
            return self.alive

    channel = RecoveringChannel()
    provider = ClaudeCodeRuntime(
        channel,
        router=router,
        idle_suspect_s=0.2,
        hard_ceiling_s=0.2,
        delivery_timeout_s=0.03,
    )
    service = ChatService(
        session_factory=factory,
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider], provider.name),
    )
    await settle_turn(service, topic_id)

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
    assert subscription.current_work is None
    assert router.subscribe(str(topic_id)) is subscription.sink

    # Late output comes from a live session; the dead verdict belongs to the
    # first turn, not to the new unsolicited activity processing these hooks.
    channel.alive = True
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
    await provider._close_topic(topic_id)
