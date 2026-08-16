"""现场必须实时 (协作软件语义): a human post persists + broadcasts INSTANTLY,
never queued behind a running agent turn — the per-topic lock serializes only
the AI part of a turn. Pre-fix, the second converse() below deadlocks until the
slow agent finishes; the wait_for(2s) would blow up."""

import asyncio
import uuid

import pytest

from app.domain.agent.chat import ChatService
from app.domain.agent.platform_failures import PROMPT_UNDELIVERED_MESSAGE
from app.domain.agent.service import (
    AgentDelta,
    AgentResult,
    AgentService,
    AgentSessionInfo,
)
from app.domain.block.repositories import BlockRepository
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


class PlatformWordedFailureAgent(AgentService):
    """A turn that fails with the PLATFORM's own wording rather than a provider's.

    The two sentences below are produced by `hooks_substrate.run_hooks_turn`
    itself — the model was never reached (undelivered) or never finished
    (timeout). Both used to fall through chat.py's `else` and be announced as
    「AI 服务返回错误」, sending whoever debugged it at the model provider."""

    def __init__(self, text: str) -> None:
        super().__init__(model="stub")
        self.text = text

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
            text=self.text,
            session_id=resume_session_id,
            usage=None,
            is_error=True,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("text", "code", "title"),
    [
        (PROMPT_UNDELIVERED_MESSAGE, "prompt_undelivered", "消息没送到芝士那边"),
        ("tmux 轮次超时", "turn_timeout", "这轮跑到时间上限，被强制结束"),
        ("device 轮次超时", "turn_timeout", "这轮跑到时间上限，被强制结束"),
    ],
)
async def test_platform_worded_failures_never_blame_the_ai_service(
    client, tmp_path, text, code, title
):
    factory = client.test_factory  # type: ignore[attr-defined]
    svc = ChatService(
        session_factory=factory,
        agent=PlatformWordedFailureAgent(text),
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

    event = next(frame for frame in frames if frame["type"] == "event_block")
    block = event["block"]
    assert block["meta"]["event_type"] == "platform_error"
    assert block["meta"]["code"] == code
    assert block["meta"]["title"] == title
    assert block["meta"]["retryable"] is True
    # The whole point: the room no longer names the AI service for a failure the
    # AI service had no part in.
    assert "AI 服务返回错误" not in block["content"]
    assert block["content"].count("。") == 1
    # 信息不能丢，只能收起来：真实原因和该怎么办都在展开区里。
    assert "不是 AI 服务" in block["meta"]["detail"]
    error = next(frame for frame in frames if frame["type"] == "error")
    assert error["code"] == code


@pytest.mark.anyio
async def test_a_message_queued_behind_a_running_turn_says_so_in_the_room(
    client, tmp_path
):
    """One turn per topic is by design; a SILENT queue behind it is not.

    While a turn holds the topic lock, every later message used to be posted and
    then vanish into `Lock.acquire()` with nothing said — indistinguishable, from
    the room, from a platform that had died. That is what「会话死掉再也无法工作」
    looked like from the outside."""
    factory = client.test_factory  # type: ignore[attr-defined]
    # SlowAgent parks mid-turn and never lets go until released — from the
    # platform's side that is exactly the shape of a wedged claude.
    agent = SlowAgent()
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

    async def _drain(content: str, sink: list[dict]) -> None:
        async for frame in svc.converse(
            topic_id=topic_id, author="u", content=content, summon=True
        ):
            sink.append(frame)

    first: list[dict] = []
    first_task = asyncio.create_task(_drain("做点事", first))
    await asyncio.wait_for(agent.started.wait(), 5)
    assert svc.topic_lock_holder(topic_id) is not None

    async def _wait_for(sink: list[dict], kind: str, task: asyncio.Task) -> dict | None:
        for _ in range(500):
            await asyncio.sleep(0.01)
            for frame in sink:
                if frame["type"] == kind:
                    return frame
            if task.done():
                task.result()  # surface the real failure, not just "no frame"
                return None
        return None

    second: list[dict] = []
    second_task = asyncio.create_task(_drain("还在吗？", second))

    queued = await _wait_for(second, "event_block", second_task)
    assert queued is not None, (
        f"a queued message must not disappear into silence; got {second}"
    )
    block = queued["block"]
    assert block["meta"]["event_type"] == "turn_queued"
    assert block["meta"]["severity"] == "info"
    assert "上一轮还没结束" in block["content"]
    assert "排在它后面" in block["content"]
    # 信息不能丢，只能收起来：为什么要等、要等到什么时候、以及"别重发"都在展开区。
    assert "不用重发" in block["meta"]["detail"]
    # The message itself still landed instantly (现场必须实时) — the queue notice
    # is in ADDITION to it, not instead of it.
    assert [f["type"] for f in second][0] == "user_block"

    # One holder, one notice: more people piling in behind the SAME stuck turn
    # must not produce a wall of identical grey lines. Their messages still land.
    third: list[dict] = []
    third_task = asyncio.create_task(_drain("？？", third))
    assert await _wait_for(third, "user_block", third_task) is not None

    async with factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic_id)
    queued_blocks = [
        b for b in blocks if (b.meta or {}).get("event_type") == "turn_queued"
    ]
    assert len(queued_blocks) == 1

    agent.release.set()
    for task in (first_task, second_task, third_task):
        await asyncio.wait_for(task, 10)
