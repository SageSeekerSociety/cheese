"""现场必须实时 (协作软件语义): a human post persists + broadcasts INSTANTLY,
never queued behind a running agent turn — the per-topic lock serializes only
the AI part of a turn. Pre-fix, the second converse() below deadlocks until the
slow agent finishes; the wait_for(2s) would blow up."""

import asyncio
import uuid

import pytest

from app.domain.agent.chat import ChatService
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.identity.handles import CHEESE_HANDLE
from app.domain.project.services import ProjectService
from app.domain.topic.repositories import TopicRepository
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, settle_turn, stub_compute


class SlowScreen(StubChannel):
    """A session that works for minutes: it takes the prompt and answers only
    once released."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.runs = 0
        self.delivered: list[str] = []
        self._answering: set[asyncio.Task] = set()

    async def send_prompt(self, screen: uuid.UUID, prompt: str) -> bool:
        if self._answering:
            # A write into a session that is already working: the transport
            # cannot tell it from the one that opened the turn, and neither can
            # a real screen.
            self.delivered.append(prompt)
            return True
        self.runs += 1
        self.last_prompt = prompt
        self.started.set()
        task = asyncio.get_running_loop().create_task(self._answer(screen))
        self._answering.add(task)
        task.add_done_callback(self._answering.discard)
        return True

    async def _answer(self, topic_id: uuid.UUID) -> None:
        await self.release.wait()
        self.starts(topic_id, session_id="s1")
        self.stops(topic_id, "done", session_id="s1")


class InstantScreen(StubChannel):
    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        del prompt, reply
        self.starts(topic_id, session_id="s-affinity")
        self.stops(topic_id, "done", session_id="s-affinity")


class ProcessNotesScreen(StubChannel):
    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        self.acknowledges(topic_id, prompt)
        self.hook(
            topic_id,
            hook_event_name="MessageDisplay",
            delta="Read workspace files.",
            _eid="process",
        )
        self.hook(
            topic_id,
            hook_event_name="MessageDisplay",
            delta="The plan is ready.",
            _eid="display",
        )
        self.stops(topic_id, "The plan is ready.", session_id="s-notes")


@pytest.mark.anyio
async def test_execution_notes_are_retained_outside_public_replies(client, tmp_path):
    factory = client.test_factory
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(ProcessNotesScreen()),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        tid = topic.id
        await session.commit()
    async for _ in svc.converse(
        topic_id=tid, author="u", content="Write a plan", summon=True
    ):
        pass
    await settle_turn(svc, tid)
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(tid)
    replies = [
        b.content
        for b in rows
        if b.kind == BlockKind.message and b.author_type == AuthorType.ai
    ]
    assert replies == []
    notes = [b for b in rows if (b.meta or {}).get("progress")]
    assert [b.content for b in notes] == ["Read workspace files.", "The plan is ready."]
    assert all(b.kind == BlockKind.event and b.meta["in_room"] is False for b in notes)


@pytest.mark.anyio
async def test_first_turn_materializes_inherited_compute_before_running(
    client, tmp_path
):
    """Changing a later default must never move an existing topic session."""
    factory = client.test_factory  # type: ignore[attr-defined]
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(InstantScreen()),
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
    await settle_turn(svc, topic_id)

    async with factory() as session:
        topic = await TopicRepository(session).get(topic_id)
        assert topic is not None
        assert topic.compute_profile == InstantScreen.name
        resumes_by = await AgentSessionService(session).resume_token(
            topic_id, CHEESE_HANDLE
        )
    assert resumes_by == "s-affinity"


@pytest.mark.anyio
async def test_post_lands_while_agent_turn_is_running(client, tmp_path):
    # Use the shared Postgres-backed factory: the merged Base.metadata now carries
    # main's PG-only sequences (e.g. discussion_seq), which SQLite cannot create.
    factory = client.test_factory  # type: ignore[attr-defined]

    agent = SlowScreen()
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(agent),
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
    await asyncio.wait_for(turn, 5)
    await settle_turn(svc, topic_id)
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    assert [b.content for b in rows if b.author_type == AuthorType.ai] == ["done"]


class FailingScreen(StubChannel):
    """A session that cannot be reached: the turn ends in an error result
    carrying the words the machine gave us, and nothing else."""

    def __init__(self, text: str, *, failure_code: str | None = None) -> None:
        super().__init__()
        self._text = text
        self._code = failure_code

    async def send_prompt(
        self, screen: uuid.UUID, prompt: str, images: list[dict] | None = None
    ) -> bool:
        del screen, prompt, images
        raise ScreenSetupError(self._text, failure_code=self._code)


@pytest.mark.anyio
async def test_a_failed_turn_says_what_failed_and_never_speaks_as_cheese(
    client, tmp_path
):
    """本卡修的那个根因：`classify_platform_failure()` 没命中就**一个结构化字段
    都没有**，于是最常见的几条（AI 接口错误 / 余额用尽 / 座位限流）全都退化成
    「朴素系统行 + 整段原话」。

    现在：没命中也照样产出 `turn_failed` 的 meta，正文只留一行——而且那一行是
    **服务自己的原话**，不是「AI 服务返回错误」这种把原因埋起来的标签。原话原样
    躺在 `meta.detail` 里。

    而且它是一条系统事件，不是芝士说的话：把机器的报错顶着芝士的名字发出去，
    读的人会以为那是它的判断。
    """
    factory = client.test_factory  # type: ignore[attr-defined]
    svc = ChatService(
        session_factory=factory,
        # 座位限流不在任何一条分类规则里 —— 这正是要测的"没命中"。
        compute=stub_compute(
            FailingScreen("You've hit your session limit · resets 12:10pm (UTC)")
        ),
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

    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="做点事", summon=True
    ):
        pass
    await settle_turn(svc, topic_id)

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    # 机器的报错不是芝士说的话。
    assert not [b for b in rows if b.author_type == AuthorType.ai]
    block = next(b for b in rows if b.kind == BlockKind.event)
    assert block.author == "system"
    # 一行，是服务的原话开头，而且整段原话不在正文里。
    assert "\n" not in block.content
    assert "session limit" in block.content
    assert "服务原话" not in block.content
    # 原话一字不差取得回来 —— 它没有第二个副本，丢了就真丢了。
    meta = block.meta
    assert meta["event_type"] == "turn_failed"
    assert meta["severity"] == "error"
    # 平台不自动重试，得有人再 @ 它。
    assert meta["who"] == "human"
    assert "session limit" in meta["detail"]
    assert meta["detail_label"] == "详细说明"


class StorageFullScreen(StubChannel):
    """The exact failure tmux skill staging produces when it hits ENOSPC. It
    must surface once as a platform event, not as an AI service blip."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def send_prompt(
        self, screen: uuid.UUID, prompt: str, images: list[dict] | None = None
    ) -> bool:
        del screen, prompt, images
        self.calls += 1
        raise ScreenSetupError(
            "tmux 后端启动失败：[Errno 28] No space left on device: "
            "'/home/nictheboy/cheese-workspaces/private/SKILL.md'"
        )


@pytest.mark.anyio
async def test_storage_exhaustion_is_a_persistent_platform_event(client, tmp_path):
    factory = client.test_factory  # type: ignore[attr-defined]
    agent = StorageFullScreen()
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(agent),
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

    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="做点事", summon=True
    ):
        pass
    await settle_turn(svc, topic_id)

    assert agent.calls == 1
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    block = next(b for b in rows if b.kind == BlockKind.event)
    assert block.meta == {
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
    assert block.content.count("。") == 1
    assert "项目文件和已完成的改动都还在" in block.meta["detail"]
    assert "/home/nictheboy" not in block.content
    assert "/home/nictheboy" not in block.meta["detail"]


class _SlowLiveScreen(SlowScreen):
    """One long-lived screen per topic, so a message that arrives while work is
    active goes into that same session."""

    name = "fake-live"
    embeds_images = False


@pytest.mark.anyio
async def test_summon_during_active_work_is_injected_without_a_second_done(
    client, tmp_path
):
    """The platform used to be stricter than the tool it drives: an interactive
    Claude Code takes input while it works, but we serialized work on top, so a
    long command made every later @ wait. A second summon now goes into the live
    session and has no independent completion boundary."""
    from app.domain.agent.compute import ComputePool
    from app.domain.block.models import consumed_turn

    factory = client.test_factory  # type: ignore[attr-defined]
    provider = _SlowLiveScreen()
    svc = ChatService(
        session_factory=factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider.runtime], provider.name),
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

    run = asyncio.create_task(summoned("user-1", "跑一个很久的命令"))
    await asyncio.wait_for(provider.started.wait(), 5)

    # Pre-fix this blocked until the active run finished.
    frames = await asyncio.wait_for(summoned("user-2", "等一下，先别跑"), 2)
    assert all(frame["type"] != "done" for frame in frames)
    assert [p.split("\n\n", 1)[0] for p in provider.delivered] == [
        "[user-2]: 等一下，先别跑"
    ]
    # Injected, not queued: still exactly one active run.
    assert provider.runs == 1

    # The receipt is still the consumed boundary (#539 decision A) — but the
    # write-accept alone must NOT stamp: until the session's UserPromptSubmit
    # comes back, the message stays pending so a session death replays it.
    async with factory() as session:
        history = await BlockRepository(session).list_for_topic(topic_id)
    merged = [b for b in history if b.content == "等一下，先别跑"]
    assert len(merged) == 1
    assert consumed_turn(merged[0]) is None

    # The session consumes the injected text → its receipt stamps the block.
    await svc.confirm_prompt_receipt(topic_id, provider.delivered[0])
    async with factory() as session:
        history = await BlockRepository(session).list_for_topic(topic_id)
    merged = [b for b in history if b.content == "等一下，先别跑"]
    assert consumed_turn(merged[0]) is not None

    provider.release.set()
    await asyncio.wait_for(run, 5)

    # Finishing the original run preserves that marker; later work will not
    # say the injected message all over again.
    async with factory() as session:
        history = await BlockRepository(session).list_for_topic(topic_id)
    merged = [b for b in history if b.content == "等一下，先别跑"]
    assert len(merged) == 1
    assert consumed_turn(merged[0]) is not None


@pytest.mark.anyio
async def test_failed_live_delivery_reports_error_then_queues_work(client, tmp_path):
    """A failed live handoff is visible before the message runs from the queue."""
    from app.domain.agent.compute import ComputePool

    factory = client.test_factory  # type: ignore[attr-defined]

    class _NoScreen(_SlowLiveScreen):
        """The live handoff fails at the transport: the second write does not
        land on the screen, so the message has to run from the queue instead."""

        name = "fake-noscreen"

        def __init__(self) -> None:
            super().__init__()
            # Set when the mid-turn write is ATTEMPTED. The test needs that
            # moment, and there is no other way to observe it: the attempt is
            # the whole subject, and it either happens while the first turn is
            # still working or it does not happen at all.
            self.tried = asyncio.Event()

        async def send_prompt(self, screen: uuid.UUID, prompt: str) -> bool:
            if self._answering:
                self.delivered.append(prompt)
                self.tried.set()
                raise ScreenSetupError("屏幕没了")
            return await super().send_prompt(screen, prompt)

    provider = _NoScreen()
    svc = ChatService(
        session_factory=factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider.runtime], provider.name),
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

    first = asyncio.create_task(summoned("user-1", "第一件事"))
    await asyncio.wait_for(provider.started.wait(), 5)

    second = asyncio.create_task(summoned("user-2", "第二件事"))
    # Wait for the write to be attempted, not for a slice of wall clock. A tenth
    # of a second used to stand in for "the second message has got as far as the
    # live handoff"; a loaded box does not honour that, and then the first turn
    # is released before the handoff happens — `deliver` finds no work in
    # flight, returns False without ever reaching the screen, and the test fails
    # on an empty `delivered` that says nothing about what it meant to check.
    await asyncio.wait_for(provider.tried.wait(), 5)
    assert not second.done()  # queued behind the lock, exactly as before

    provider.release.set()
    await asyncio.wait_for(first, 5)
    second_frames = await asyncio.wait_for(second, 5)
    assert provider.runs == 2
    assert [p.split("\n\n", 1)[0] for p in provider.delivered] == ["[user-2]: 第二件事"]

    fallback_frames = [
        frame
        for frame in second_frames
        if frame["type"] == "event_block"
        and frame["block"]["meta"].get("event_type") == "delivery_fallback"
    ]
    assert len(fallback_frames) == 1
    fallback = fallback_frames[0]["block"]
    assert "没能送进正在进行的会话" in fallback["content"]
    assert "队列" in fallback["content"]  # 说了去向，人才知道消息没丢
    assert fallback["meta"]["severity"] == "error"
    assert fallback["meta"]["who"] == "platform"
    assert all(frame["type"] != "error" for frame in second_frames)

    async with factory() as session:
        history = await BlockRepository(session).list_for_topic(topic_id)
    persisted = [
        block
        for block in history
        if (block.meta or {}).get("event_type") == "delivery_fallback"
    ]
    assert len(persisted) == 1


@pytest.mark.anyio
async def test_midturn_message_stays_pending_until_its_receipt(
    client, tmp_path, monkeypatch
):
    """#539 decision A: deliver() trusts the transport's write-accept, so the
    consumed stamp moves to the UserPromptSubmit receipt. Before the receipt
    the message stays pending (a session death replays it — 宁可重复不可丢失);
    only a receipt carrying the SAME injected text stamps it."""
    from app.domain.block.models import consumed_turn

    factory = client.test_factory  # type: ignore[attr-defined]
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(InstantScreen()),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id: uuid.UUID = topic.id
        await session.commit()

    _payloads, block_id, block_ids = await svc.post_user_message(
        topic_id, author="u", content="改一下配色", turn_id=None, reply_to=None
    )

    delivered_texts: list[str] = []

    async def fake_deliver(tid, text, images=None):
        delivered_texts.append(text)
        return True

    monkeypatch.setattr(svc._compute, "deliver", fake_deliver)
    turn_id = uuid.uuid4()
    svc._active_turn_ids[topic_id] = turn_id

    assert (
        await svc.merge_into_running_turn(topic_id, block_ids, "改一下配色", "u")
        is True
    )
    assert len(delivered_texts) == 1

    async def _consumed() -> bool:
        async with factory() as session:
            rows = await BlockRepository(session).list_for_topic(topic_id)
        row = next(b for b in rows if b.id == block_id)
        return consumed_turn(row) is not None

    # Write accepted but not yet consumed: must stay pending.
    assert await _consumed() is False
    # A receipt for some OTHER input must not stamp this message.
    await svc.confirm_prompt_receipt(topic_id, "别的输入")
    assert await _consumed() is False
    # The matching receipt stamps it.
    await svc.confirm_prompt_receipt(topic_id, delivered_texts[0])
    assert await _consumed() is True
