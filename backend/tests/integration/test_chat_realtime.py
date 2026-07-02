"""现场必须实时 (协作软件语义): a human post persists + broadcasts INSTANTLY,
never queued behind a running agent turn — the per-topic lock serializes only
the AI part of a turn. Pre-fix, the second converse() below deadlocks until the
slow agent finishes; the wait_for(2s) would blow up."""

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from app.core.db import Base
from app.domain.agent.chat import ChatService
from app.domain.agent.service import AgentDelta, AgentResult, AgentService
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService


class SlowAgent(AgentService):
    """Parks mid-turn until released — simulates 芝士 working for minutes."""

    def __init__(self) -> None:
        super().__init__(model="stub")
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream_reply(
        self, *, prompt, system_prompt, cwd, resume_session_id,
        sandbox=None, allowed_tools=None, **_,
    ):
        self.started.set()
        yield AgentDelta(text="thinking…")
        await self.release.wait()
        yield AgentResult(text="done", session_id="s1", usage=None)


@pytest.mark.anyio
async def test_post_lands_while_agent_turn_is_running(tmp_path):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    agent = SlowAgent()
    svc = ChatService(
        session_factory=factory,
        agent=agent,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )

    async with factory() as session:
        project = await ProjectService(session).create(
            name="P", owner_handle="user-1"
        )
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
    structured error whose text is the provider's raw message."""

    def __init__(self) -> None:
        super().__init__(model="stub")

    async def stream_reply(
        self, *, prompt, system_prompt, cwd, resume_session_id,
        sandbox=None, allowed_tools=None, **_,
    ):
        yield AgentResult(
            text="You've hit your session limit · resets 12:10pm (UTC)",
            session_id="s1",
            usage=None,
            is_error=True,
        )


@pytest.mark.anyio
async def test_error_result_never_becomes_cheeses_reply(tmp_path):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    svc = ChatService(
        session_factory=factory,
        agent=LimitAgent(),
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
    assert "AI 服务返回错误" in ev["block"]["content"]

    # Persisted state: user message + the system event, no cheese message.
    from app.domain.block.repositories import BlockRepository

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    authors = [(b.author, b.kind.value) for b in rows]
    assert ("cheese", "message") not in authors
    assert ("system", "event") in authors
