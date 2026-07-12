"""Test fixtures.

Uses an in-memory SQLite database (StaticPool so every connection shares the
same in-memory DB) and a stub agent so deterministic tests never call the live
model. The live agent is exercised separately by the smoke script.
"""

import asyncio
import os
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from app.api.deps import get_chat_service, get_turn_runner
from app.core.config import settings
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

# Tests always run on the DB memory backend: the openviking backend holds an
# exclusive data-dir lock (owned by the dev server when it's running), and
# tests must not depend on — or corrupt — the live memory store.
settings.memory_backend = "db"
# Tests exercise the real authz enforcement regardless of the dev .env (which
# ships it OFF for the conservative dogfood rollout). Same leak class as the
# memory backend above: the .env value must not decide test behavior.
settings.authz_enforce_topic_access = True


def wait_turns_idle() -> None:
    """Block until background turns (e.g. the 分身 kickoff a /split submits)
    finish: they run on the TestClient portal loop and write to the shared
    in-memory SQLite — racing them with further requests makes flakes."""
    runner = get_turn_runner()
    for _ in range(250):
        if runner.active_turns() == 0:
            return
        time.sleep(0.02)


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
        sandbox=None,
        allowed_tools=None,
        **_,
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
def client(_pg_schema, stub_agent: StubAgent, tmp_path) -> Iterator[TestClient]:
    # Real PostgreSQL (not sqlite): the merged models use PG-native JSONB,
    # Sequences and ENUM types that sqlite's compiler can't render, and the schema
    # is defined by the alembic migrations (create_all can't build the pg ENUMs).
    # NullPool → every connection is created fresh in its calling event loop, so
    # the TestClient's portal loop and this fixture's setup loop never share an
    # asyncpg connection (which is loop-bound). Isolation is per-test TRUNCATE.
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    test_factory = async_sessionmaker(engine, expire_on_commit=False)

    asyncio.run(_truncate_all(engine))

    # agent-as-user baseline (P1): 芝士 is a real user with a platform agent-
    # binding — seeded by the migration in prod, re-seeded here after the truncate.
    async def _seed_agent_user() -> None:
        from app.domain.identity.services import IdentityService

        async with test_factory() as session:
            await IdentityService(session).ensure_agent_user()
            await session.commit()

    asyncio.run(_seed_agent_user())

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
        # Drain background turns BEFORE leaving the TestClient context:
        # disposing the engine under a running kickoff turn makes flakes.
        wait_turns_idle()

    app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


# --- PostgreSQL test-DB plumbing (shared by client / python_client) -----------

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://cheesex:cheesex@localhost:5433/cheesex_test",
)


async def _truncate_all(engine) -> None:
    """Wipe every table for a clean per-test slate (fast; keeps the schema)."""
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    if not tables:
        return
    async with engine.begin() as conn:
        await conn.exec_driver_sql(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")


@pytest.fixture(scope="session")
def _pg_schema():
    """Build the test DB schema ONCE per session via the alembic migrations —
    exactly how production is built (create_all can't render the pg ENUM types).
    Requires the Docker postgres on :5433. Runs in a subprocess so alembic's env
    picks up the test DB URL cleanly."""
    import subprocess
    from pathlib import Path
    from urllib.parse import urlparse

    backend_dir = Path(__file__).resolve().parent.parent
    # DATABASE_URL maps to settings.database_url, which alembic/env.py reads.
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    # Drop + recreate the public schema so each session starts from bare metal,
    # then migrate to head. Uses the sync psql in the running container.
    db_name = urlparse(TEST_DATABASE_URL.replace("+asyncpg", "")).path.lstrip("/")
    subprocess.run(
        ["docker", "exec", "cheesex-pg", "psql", "-U", "cheesex", "-d", db_name,
         "-c", "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"],
        check=True, capture_output=True,
    )
    subprocess.run(
        [str(backend_dir / ".venv/bin/alembic"), "upgrade", "head"],
        cwd=backend_dir,
        env=env,
        check=True,
        capture_output=True,
    )
    yield


@pytest.fixture
async def python_client(_pg_schema, stub_agent: StubAgent, tmp_path):
    """Async httpx client bound to the app over ASGI — the async counterpart to
    `client`. Inherited contract/route tests written against the main backend use
    it. Same postgres test DB + truncate isolation + agent seed as `client`, but
    awaitable inside anyio tests."""
    from httpx import ASGITransport, AsyncClient

    from app.domain.identity.services import IdentityService

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    test_factory = async_sessionmaker(engine, expire_on_commit=False)
    await _truncate_all(engine)
    async with test_factory() as session:
        await IdentityService(session).ensure_agent_user()
        await session.commit()

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

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Cheese-Token": SANDBOX_TOKEN},
    ) as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


# --- shared seed helpers -----------------------------------------------------


def seed_space(client: TestClient, name: str = "信院") -> int:
    """Insert a 知是 Space row directly and return its int id.

    The cheesex ``POST /api/spaces`` uuid stub was retired in the fusion merge
    (unify P1b/c: one Space = main int). The task-template market still lives on
    top of a Space, so tests that need one seed it through the DB here.
    """
    import asyncio as _asyncio
    from datetime import UTC, datetime

    from app.domain.space.models import Space

    holder: dict[str, int] = {}

    async def _seed() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            now = datetime.now(UTC)
            space = Space(
                name=name,
                intro="",
                description="",
                announcements=[],
                task_templates=[],
                created_at=now,
                updated_at=now,
            )
            session.add(space)
            await session.flush()
            holder["id"] = space.id
            await session.commit()

    _asyncio.run(_seed())
    return holder["id"]


def seed_user(client: TestClient, handle: str) -> str:
    """Get-or-create a real 知是 User for ``handle`` and return a session token
    whose ``sub`` is the int user id (so ActorResolver resolves ``user_id``).

    The cheesex POST /api/users/login handle-login was retired in the fusion
    merge (unify P3). Flows that need a genuine logged-in human with a DB-backed
    user id (e.g. device approval binding an owner) use this instead of a bare
    handle token.
    """
    import asyncio as _asyncio

    from app.common.auth import create_access_token
    from app.domain.user.repositories import UserRepository

    holder: dict[str, int] = {}

    async def _seed() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            repo = UserRepository(session)
            user = await repo.get_by_username(handle)
            if user is None:
                user = await repo.create_user(
                    username=handle, email=f"{handle}@example.com"
                )
            holder["id"] = user.id
            await session.commit()

    _asyncio.run(_seed())
    return create_access_token(holder["id"], handle=handle)
