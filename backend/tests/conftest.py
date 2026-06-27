"""Test fixtures.

Uses an in-memory SQLite database (StaticPool so every connection shares the
same in-memory DB) and a stub agent so deterministic tests never call the live
model. The live agent is exercised separately by the smoke script.
"""

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from app.api.deps import get_chat_service
from app.core.db import Base, get_db
from app.core.sandbox_auth import SANDBOX_TOKEN
from app.domain.agent.chat import ChatService
from app.domain.agent.service import (
    AgentDelta,
    AgentResult,
    AgentService,
    AgentUsage,
)
from app.main import app


class StubAgent(AgentService):
    """Deterministic agent: streams two deltas then a final result.

    Records the last system_prompt so tests can assert memory injection.
    """

    def __init__(self) -> None:
        super().__init__(model="stub")
        self.last_system_prompt: str | None = None
        self.last_resume_session_id: str | None = None
        self.last_prompt: str | None = None

    async def stream_reply(
        self,
        *,
        prompt,
        system_prompt,
        cwd,
        resume_session_id,
        mcp_servers=None,
        allowed_tools=None,
    ):
        self.last_system_prompt = system_prompt
        self.last_resume_session_id = resume_session_id
        self.last_prompt = prompt
        yield AgentDelta(text="Hello ")
        yield AgentDelta(text="world")
        yield AgentResult(
            text="Hello world",
            session_id="sess-test-1",
            usage=AgentUsage(
                model="stub", input_tokens=10, output_tokens=5, cost_usd=0.001
            ),
        )


@pytest.fixture
def stub_agent() -> StubAgent:
    return StubAgent()


@pytest.fixture
def client(stub_agent: StubAgent, tmp_path) -> Iterator[TestClient]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_factory = async_sessionmaker(engine, expire_on_commit=False)

    # aiosqlite keeps its single StaticPool connection in a dedicated thread,
    # so creating the schema here (own loop) is visible to the TestClient loop.
    async def _create_schema() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_schema())

    async def override_get_db():
        async with test_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    def override_get_chat_service() -> ChatService:
        return ChatService(
            session_factory=test_factory,
            agent=stub_agent,
            base_system_prompt="你是芝士。",
            workspace_root=str(tmp_path / "ws"),
        )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_chat_service] = override_get_chat_service

    with TestClient(app) as c:
        # The cheese write-API is token-gated (app.main.cheese_token_gate); send
        # the secret on every test request so contract tests exercising those
        # endpoints (doc/split/decision/...) aren't rejected with 401.
        c.headers["X-Cheese-Token"] = SANDBOX_TOKEN
        # Expose the factory so tests can seed data (e.g. memory entries).
        c.test_factory = test_factory  # type: ignore[attr-defined]
        yield c

    app.dependency_overrides.clear()
    asyncio.run(engine.dispose())
