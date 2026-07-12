"""现场必须实时 (协作软件语义): a human post persists + broadcasts INSTANTLY,
never queued behind a running agent turn — the per-topic lock serializes only
the AI part of a turn. Pre-fix, the second converse() below deadlocks until the
slow agent finishes; the wait_for(2s) would blow up."""

import asyncio
import uuid

import pytest

from app.domain.agent.chat import ChatService
from app.domain.agent.service import (
    AgentDelta,
    AgentResult,
    AgentService,
    AgentSessionInfo,
)
from app.domain.project.services import ProjectService
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
    assert err["persisted"] is True and "session limit" in err["message"]
    ev = next(f for f in frames if f["type"] == "event_block")
    assert ev["block"]["author"] == "system"
    # Structured rate-limit → platform wording with the reset in 北京时间,
    # raw provider text quoted for the record.
    assert "额度用完了" in ev["block"]["content"]
    assert "北京时间 08-12 20:00" in ev["block"]["content"]
    assert "服务原话" in ev["block"]["content"]

    # Persisted state: user message + the system event, no cheese message.
    from app.domain.block.repositories import BlockRepository

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    authors = [(b.author, b.kind.value) for b in rows]
    assert ("cheese", "message") not in authors
    assert ("system", "event") in authors


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
