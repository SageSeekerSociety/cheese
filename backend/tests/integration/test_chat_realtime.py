"""现场必须实时: posts and summons never queue behind a topic-wide turn lock."""

import asyncio
import uuid

import pytest

from app.domain.agent.chat import ChatService
from app.domain.agent.hook_events import HookRouter
from app.domain.agent.hooks_substrate import HooksTurnProvider
from app.domain.agent.service import (
    AgentDelta,
    AgentResult,
    AgentService,
    AgentSessionInfo,
)
from app.domain.project.services import ProjectService
from app.domain.topic.repositories import TopicRepository
from app.domain.topic.services import TopicService


class SlowAgent(AgentService):
    """Parks mid-turn until released — simulates 芝士 working for minutes."""

    def __init__(self) -> None:
        super().__init__(model="stub")
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream_reply(
        self,
        *,
        prompt,
        system_prompt,
        cwd,
        resume_session_id,
        sandbox=None,
        allowed_tools=None,
        **_,
    ):
        self.started.set()
        yield AgentDelta(text="thinking…")
        await self.release.wait()
        yield AgentResult(text="done", session_id="s1", usage=None)


class InstantAgent(AgentService):
    async def stream_reply(
        self,
        *,
        prompt,
        system_prompt,
        cwd,
        resume_session_id,
        sandbox=None,
        allowed_tools=None,
        **_,
    ):
        yield AgentSessionInfo(session_id="s-affinity")
        yield AgentResult(text="done", session_id="s-affinity", usage=None)


@pytest.mark.anyio
async def test_first_turn_materializes_inherited_compute_before_running(
    client, tmp_path
):
    """Changing a later default must never move an existing topic session."""
    factory = client.test_factory  # type: ignore[attr-defined]
    svc = ChatService(
        session_factory=factory,
        agent=InstantAgent(model="stub"),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )

    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        project.settings = {"compute_profile": "local-docker"}
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id = topic.id
        await session.commit()

    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="start", summon=True
    ):
        pass

    async with factory() as session:
        topic = await TopicRepository(session).get(topic_id)
        assert topic is not None
        assert topic.compute_profile == "local-docker"
        assert topic.session_id == "s-affinity"


@pytest.mark.anyio
async def test_post_lands_while_agent_turn_is_running(client, tmp_path):
    # Use the shared Postgres-backed factory: the merged Base.metadata now carries
    # main's PG-only sequences (e.g. discussion_seq), which SQLite cannot create.
    factory = client.test_factory  # type: ignore[attr-defined]

    agent = SlowAgent()
    svc = ChatService(
        session_factory=factory,
        agent=agent,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )

    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="user-1")
        topic = await TopicService(session).create(
            project_id=project.id, title="讨论", created_by="user-1"
        )
        topic_id: uuid.UUID = topic.id
        await session.commit()

    # A summoned turn that parks mid-stream, holding the topic's turn lock.
    async def summoned() -> list[dict]:
        return [
            f
            async for f in svc.converse(
                topic_id=topic_id, author="user-1", content="做点事", summon=True
            )
        ]

    turn = asyncio.create_task(summoned())
    await asyncio.wait_for(agent.started.wait(), 5)

    # While 芝士 is working, another human's plain post must land at once.
    async def post() -> list[dict]:
        return [
            f
            async for f in svc.converse(
                topic_id=topic_id, author="user-2", content="我插一句", summon=False
            )
        ]

    frames = await asyncio.wait_for(post(), 2)  # pre-fix: deadlocks here
    assert [f["type"] for f in frames] == ["user_block", "done"]
    assert frames[0]["block"]["content"] == "我插一句"
    assert frames[0]["block"]["author"] == "user-2"

    # The parked turn finishes normally afterwards.
    agent.release.set()
    turn_frames = await asyncio.wait_for(turn, 5)
    assert any(f["type"] == "assistant_block" for f in turn_frames)


class LimitAgent(AgentService):
    """Simulates the seat rate-limit: the run 'succeeds' but the result is a
    structured error — rate_limit carries the reset as a unix timestamp."""

    def __init__(self, **extra) -> None:
        super().__init__(model="stub")
        self._extra = extra

    async def stream_reply(
        self,
        *,
        prompt,
        system_prompt,
        cwd,
        resume_session_id,
        sandbox=None,
        allowed_tools=None,
        **_,
    ):
        yield AgentResult(
            text="You've hit your session limit · resets 12:10pm (UTC)",
            session_id="s1",
            usage=None,
            is_error=True,
            **self._extra,
        )


@pytest.mark.anyio
async def test_error_result_never_becomes_cheeses_reply(client, tmp_path):
    # Use the shared Postgres-backed factory: the merged Base.metadata now carries
    # main's PG-only sequences (e.g. discussion_seq), which SQLite cannot create.
    factory = client.test_factory  # type: ignore[attr-defined]

    # 1755000000 = 2025-08-12 20:00 Asia/Shanghai — the platform must translate
    # the structured reset timestamp into 北京时间 wording.
    svc = ChatService(
        session_factory=factory,
        agent=LimitAgent(
            rate_limit={
                "status": "rejected",
                "resets_at": 1755000000,
                "type": "five_hour",
            }
        ),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id = topic.id
        await session.commit()

    frames = [
        f
        async for f in svc.converse(
            topic_id=topic_id, author="u", content="做点事", summon=True
        )
    ]
    kinds = [f["type"] for f in frames]
    assert "assistant_block" not in kinds  # the raw provider text is NOT 芝士 speaking
    err = next(f for f in frames if f["type"] == "error")
    # The transient frame carries the same one line the room got; the provider's
    # own words live in the persisted block's meta (see below).
    assert err["persisted"] is True and "额度用完了" in err["message"]
    ev = next(f for f in frames if f["type"] == "event_block")
    assert ev["block"]["author"] == "system"
    # Structured rate-limit → platform wording with the reset in 北京时间.
    assert "额度用完了" in ev["block"]["content"]
    assert "北京时间 08-12 20:00" in ev["block"]["content"]
    # 平台提示统一契约: 服务原话不再拼进正文（那让一条朴素系统行动辄七八行），
    # 它原样躺在 meta.detail 里等人展开 —— 信息不能丢，只能收起来。
    assert "服务原话" not in ev["block"]["content"]
    meta = ev["block"]["meta"]
    assert meta["event_type"] == "turn_failed"
    # 座位限流会自动续跑，所以这条是"平台自愈"，不需要人管。
    assert meta["who"] == "platform"
    assert "session limit" in meta["detail"]

    # Persisted state: user message + the system event, no cheese message.
    from app.domain.block.repositories import BlockRepository

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    authors = [(b.author, b.kind.value) for b in rows]
    assert ("cheese", "message") not in authors
    assert ("system", "event") in authors


@pytest.mark.anyio
async def test_unclassified_failure_still_gets_meta_and_hides_the_raw_words(
    client, tmp_path
):
    """本卡修的那个根因：`classify_platform_failure()` 没命中就**一个结构化字段
    都没有**，于是最常见的几条（AI 接口错误 / 余额用尽 / 座位限流）全都退化成
    「朴素系统行 + 整段原话」。

    现在：没命中也照样产出 `turn_failed` 的 meta，正文只留一行，原话原样躺在
    `meta.detail` 里。"""
    factory = client.test_factory  # type: ignore[attr-defined]
    svc = ChatService(
        session_factory=factory,
        # 400 不在任何一条分类规则里 —— 这正是要测的"没命中"。
        agent=LimitAgent(api_error_status=400),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        project = await ProjectService(session).create(name="P2", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T2", created_by="u"
        )
        topic_id = topic.id
        await session.commit()

    frames = [
        f
        async for f in svc.converse(
            topic_id=topic_id, author="u", content="做点事", summon=True
        )
    ]
    ev = next(f for f in frames if f["type"] == "event_block")
    block = ev["block"]
    assert block["author_type"] == "system" and block["kind"] == "event"
    # 一行，而且原话不在里面。
    assert "\n" not in block["content"]
    assert "服务原话" not in block["content"]
    assert "session limit" not in block["content"]
    assert "HTTP 400" in block["content"]
    # 原话一字不差取得回来 —— 它没有第二个副本，丢了就真丢了。
    meta = block["meta"]
    assert meta["event_type"] == "turn_failed"
    assert meta["severity"] == "error"
    # 400 没有自动续跑，得有人再 @ 它。
    assert meta["who"] == "human"
    assert "session limit" in meta["detail"]
    assert meta["detail_label"] == "详细说明"


class StorageFullAgent(AgentService):
    """The exact provider-result shape produced when tmux skill staging hits
    ENOSPC. It must surface once as a platform event, not be retried as an AI
    service blip."""

    def __init__(self) -> None:
        super().__init__(model="stub")
        self.calls = 0

    async def stream_reply(
        self,
        *,
        prompt,
        system_prompt,
        cwd,
        resume_session_id,
        sandbox=None,
        allowed_tools=None,
        **_,
    ):
        self.calls += 1
        yield AgentResult(
            text=(
                "tmux 后端启动失败：[Errno 28] No space left on device: "
                "'/home/nictheboy/cheese-workspaces/private/SKILL.md'"
            ),
            session_id=resume_session_id,
            usage=None,
            is_error=True,
        )


@pytest.mark.anyio
async def test_storage_exhaustion_is_a_persistent_platform_event(client, tmp_path):
    factory = client.test_factory  # type: ignore[attr-defined]
    agent = StorageFullAgent()
    svc = ChatService(
        session_factory=factory,
        agent=agent,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id = topic.id
        await session.commit()

    frames = [
        frame
        async for frame in svc.converse(
            topic_id=topic_id, author="u", content="做点事", summon=True
        )
    ]

    assert agent.calls == 1
    event = next(frame for frame in frames if frame["type"] == "event_block")
    assert event["block"]["meta"] == {
        "event_type": "platform_error",
        "code": "storage_exhausted",
        "severity": "error",
        "title": "运行环境存储空间不足",
        "retryable": True,
        # 平台提示统一契约: 卡面留一句，解释性的那几句收进 detail 由前端折叠。
        "detail": (
            "项目文件和已完成的改动都还在。平台正在清理临时空间，"
            "请稍后再 @芝士 继续；若持续出现，请联系管理员。"
        ),
        "detail_label": "详细说明",
    }
    # 卡面是一句话；「已完成的改动都还在」这条信息没丢，它在展开区里。
    assert event["block"]["content"].count("。") == 1
    assert "项目文件和已完成的改动都还在" in event["block"]["meta"]["detail"]
    assert "/home/nictheboy" not in event["block"]["content"]
    assert "/home/nictheboy" not in event["block"]["meta"]["detail"]
    error = next(frame for frame in frames if frame["type"] == "error")
    assert error == {
        "type": "error",
        "code": "storage_exhausted",
        "message": event["block"]["content"],
        "persisted": True,
    }


class FlakyAgent(AgentService):
    """First call: transient HTTP 529 error result. Second call: normal reply.
    The turn must auto-retry and the user only ever sees the good reply."""

    def __init__(self) -> None:
        super().__init__(model="stub")
        self.calls = 0

    async def stream_reply(
        self,
        *,
        prompt,
        system_prompt,
        cwd,
        resume_session_id,
        sandbox=None,
        allowed_tools=None,
        **_,
    ):
        self.calls += 1
        if self.calls == 1:
            yield AgentResult(
                text="API Error (529 Overloaded)",
                session_id="s1",
                usage=None,
                is_error=True,
                api_error_status=529,
            )
        else:
            yield AgentDelta(text="搞定")
            yield AgentResult(text="搞定", session_id="s1", usage=None)


@pytest.mark.anyio
async def test_transient_api_error_is_retried(client, tmp_path, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)  # skip the real backoff
    # Use the shared Postgres-backed factory: the merged Base.metadata now carries
    # main's PG-only sequences (e.g. discussion_seq), which SQLite cannot create.
    factory = client.test_factory  # type: ignore[attr-defined]

    agent = FlakyAgent()
    svc = ChatService(
        session_factory=factory,
        agent=agent,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id = topic.id
        await session.commit()

    frames = [
        f
        async for f in svc.converse(
            topic_id=topic_id, author="u", content="做点事", summon=True
        )
    ]
    kinds = [f["type"] for f in frames]
    assert agent.calls == 2  # retried exactly once
    assert "error" not in kinds  # the 529 never surfaced
    final = next(f for f in frames if f["type"] == "assistant_block")
    assert final["block"]["content"] == "搞定"


async def _fast_sleep(_s: float) -> None:
    return None


class MidCrashAgent(AgentService):
    """Announces its session, streams some work, then dies mid-stream."""

    def __init__(self) -> None:
        super().__init__(model="stub")

    async def stream_reply(
        self,
        *,
        prompt,
        system_prompt,
        cwd,
        resume_session_id,
        sandbox=None,
        allowed_tools=None,
        **_,
    ):
        yield AgentSessionInfo(session_id="s-partial")
        yield AgentDelta(text="干着呢")
        raise RuntimeError("connection lost")


@pytest.mark.anyio
async def test_mid_stream_crash_saves_session_pointer(client, tmp_path, monkeypatch):
    """Resume, not replay: a turn that dies after producing output must leave
    the topic pointing at the PARTIAL session, so the next summon continues
    from where it stopped instead of redoing (and re-side-effecting) the work."""
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    # Use the shared Postgres-backed factory: the merged Base.metadata now carries
    # main's PG-only sequences (e.g. discussion_seq), which SQLite cannot create.
    factory = client.test_factory  # type: ignore[attr-defined]

    svc = ChatService(
        session_factory=factory,
        agent=MidCrashAgent(),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id = topic.id
        await session.commit()

    with pytest.raises(RuntimeError):
        async for _ in svc.converse(
            topic_id=topic_id, author="u", content="做点事", summon=True
        ):
            pass

    from app.domain.topic.repositories import TopicRepository

    async with factory() as session:
        fresh = await TopicRepository(session).get(topic_id)
    assert fresh is not None and fresh.session_id == "s-partial"


class _SlowLiveScreenProvider(HooksTurnProvider[str]):
    """A hooks-style backend: one long-lived screen per topic, so a message that
    arrives mid-turn can be injected into the turn already running."""

    name = "fake-live"

    def __init__(self) -> None:
        self.router = HookRouter()
        super().__init__(router=self.router)
        self.started = asyncio.Event()
        self.injected: list[str] = []

    async def _ensure_ready(self, **kwargs):
        return "screen"

    async def _send_prompt(self, screen, prompt):
        self.injected.append(prompt)
        self.started.set()

    def finish(self, topic_id: uuid.UUID) -> None:
        self.router.push(
            str(topic_id),
            {
                "hook_event_name": "MessageDisplay",
                "delta": "done",
                "_eid": "done-message",
            },
        )
        self.router.push(
            str(topic_id),
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "done",
                "session_id": "s1",
                "_eid": "done-stop",
            },
        )


@pytest.mark.anyio
async def test_summon_during_a_running_turn_is_injected_not_queued(client, tmp_path):
    """The platform used to be stricter than the tool it drives: an interactive
    Claude Code takes input while it works, but we serialized turns on top, so a
    long command made every later @ wait the whole turn out. A second summon now
    goes INTO the running turn — one turn, message delivered in milliseconds."""
    from app.domain.agent.compute import ComputePool
    from app.domain.block.models import consumed_turn
    from app.domain.block.repositories import BlockRepository

    factory = client.test_factory  # type: ignore[attr-defined]
    provider = _SlowLiveScreenProvider()
    svc = ChatService(
        session_factory=factory,
        agent=InstantAgent(model="stub"),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider], provider.name),  # type: ignore[list-item]
    )

    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="user-1")
        topic = await TopicService(session).create(
            project_id=project.id, title="讨论", created_by="user-1"
        )
        topic_id: uuid.UUID = topic.id
        await session.commit()

    async def summoned(author: str, content: str) -> list[dict]:
        return [
            f
            async for f in svc.converse(
                topic_id=topic_id, author=author, content=content, summon=True
            )
        ]

    from app.domain.agent.runtime import get_broker

    first = await summoned("user-1", "跑一个很久的命令")
    await asyncio.wait_for(provider.started.wait(), 5)

    second = await asyncio.wait_for(summoned("user-2", "等一下，先别跑"), 2)
    assert first and second
    assert len(provider.injected) == 2
    assert "跑一个很久的命令" in provider.injected[0]
    assert "等一下，先别跑" in provider.injected[1]

    async with factory() as session:
        before_stop = await BlockRepository(session).list_for_topic(topic_id)
    pending_second = [b for b in before_stop if b.content == "等一下，先别跑"]
    assert len(pending_second) == 1
    assert consumed_turn(pending_second[0]) is None

    async with get_broker().subscribe(str(topic_id)) as room:
        provider.finish(topic_id)
        assert (await asyncio.wait_for(room.get(), 2))["type"] == "assistant_block"
        assert await asyncio.wait_for(room.get(), 2) == {"type": "done"}

    # The injected message counts as read by the turn that took it, so the next
    # turn does not say it all over again.
    async with factory() as session:
        history = await BlockRepository(session).list_for_topic(topic_id)
    merged = [b for b in history if b.content == "等一下，先别跑"]
    assert len(merged) == 1
    assert consumed_turn(merged[0]) is not None
    await provider.drop_subscription(topic_id)


@pytest.mark.anyio
async def test_noninteractive_provider_answers_both_messages_in_one_turn(
    client, tmp_path
):
    """A backend with no live screen cannot take an injected message, so a second
    summon waits instead of racing it. It then answers only what is genuinely
    still unanswered — never the message the running turn already took, which is
    what an unlocked prompt build produces, since the consumed stamp only lands
    when the turn finishes.

    This is the invariant `test_turn_message_window` guards from the other side.
    It costs a wait only where there is nothing to inject into: on the hooks
    backends the same lock is released as soon as the prompt is injected."""
    from app.domain.agent.compute import ComputePool

    factory = client.test_factory  # type: ignore[attr-defined]

    class _NoScreen:
        name = "fake-noscreen"
        embeds_images = True

        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.turns = 0
            self.prompts: list[str] = []

        def available(self) -> bool:
            return True

        async def run_turn(self, **kwargs):
            self.turns += 1
            self.prompts.append(kwargs["prompt"])
            self.started.set()
            await self.release.wait()
            yield AgentResult(text="done", session_id="s1", usage=None)

        async def deliver(self, topic_id: uuid.UUID, text: str) -> bool:
            return False

        def checkpoint(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
            return

    provider = _NoScreen()
    svc = ChatService(
        session_factory=factory,
        agent=InstantAgent(model="stub"),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider], provider.name),  # type: ignore[list-item]
    )

    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="user-1")
        topic = await TopicService(session).create(
            project_id=project.id, title="讨论", created_by="user-1"
        )
        topic_id: uuid.UUID = topic.id
        await session.commit()

    async def summoned(author: str, content: str) -> list[dict]:
        return [
            f
            async for f in svc.converse(
                topic_id=topic_id, author=author, content=content, summon=True
            )
        ]

    turn = asyncio.create_task(summoned("user-1", "第一件事"))
    await asyncio.wait_for(provider.started.wait(), 5)

    second = asyncio.create_task(summoned("user-2", "第二件事"))
    await asyncio.sleep(0.05)
    # Nothing to inject into, so it waits for the running turn instead of
    # starting a second one against the same topic.
    assert provider.turns == 1
    assert not second.done()

    provider.release.set()
    await asyncio.wait_for(turn, 5)
    await asyncio.wait_for(second, 5)

    # 第二件事 was posted after the first turn had already built its prompt, so it
    # is genuinely unanswered and does get its own turn. What must NOT happen is
    # the first message being answered twice — which is exactly what an unlocked
    # prompt build produces, because the consumed stamp lands at turn end.
    assert provider.turns == 2
    assert "第一件事" in provider.prompts[0]
    assert "第二件事" not in provider.prompts[0]
    assert "第二件事" in provider.prompts[1]
    assert "第一件事" not in provider.prompts[1]
