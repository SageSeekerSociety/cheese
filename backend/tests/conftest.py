"""Test fixtures.

DB-backed tests run on real PostgreSQL (the merged models need PG-native
JSONB/Sequence/ENUM that sqlite can't build; the schema is the alembic migrations).
FULL xdist isolation: every worker gets its OWN databases, so shared sequences /
reference rows / data never race across workers. Two DBs per worker because the
two harnesses can't share one:
  * ``cheesex_test[_<worker>]``    — the integration harness (per-test transactional
    rollback on a session-long connection); the app engines bind here.
  * ``cheesex_test[_<worker>]_c``  — client / python_client (TRUNCATE + a real
    session factory: ChatService spins up its own sessions and background turns
    COMMIT, which rollback can't isolate; truncate would also deadlock against the
    integration harness's open transaction, hence a separate DB).
A stub agent keeps tests off the live model.
"""

import asyncio
import os
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Strip inherited git env. When the suite runs from the pre-commit HOOK it executes
# DURING `git commit`, which exports GIT_DIR / GIT_INDEX_FILE / GIT_WORK_TREE for
# the hook. The workspace tests (and the app's git ops) spawn `git` subprocesses;
# those vars take precedence over `git -C <tmprepo>` and would hijack them onto the
# MAIN repo — green when run directly, red only under the hook. Clear them so tests
# always get a clean, cwd-driven git context.
for _k in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_k]

# Bind BOTH app engine modules (app.core.db, app.db.session) to THIS worker's
# integration DB — must happen before any app import (they build their engine from
# settings.database_url at import time). ---------------------------------------
from app.core.config import settings  # noqa: E402

_XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")  # "gw0"… or "" (serial)
_DB_SUFFIX = f"_{_XDIST_WORKER}" if _XDIST_WORKER else ""
_INTG_DB_NAME = f"cheesex_test{_DB_SUFFIX}"
_CLIENT_DB_NAME = f"cheesex_test{_DB_SUFFIX}_c"
_PG_BASE = "postgresql+asyncpg://cheesex:cheesex@localhost:5433"
settings.database_url = f"{_PG_BASE}/{_INTG_DB_NAME}"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", f"{_PG_BASE}/{_CLIENT_DB_NAME}")

import app.models  # noqa: F401, E402  (registers all tables on Base.metadata)
from app.api.deps import get_broker, get_chat_service, get_turn_runner  # noqa: E402
from app.core.db import Base, get_db  # noqa: E402
from app.core.sandbox_auth import SANDBOX_TOKEN  # noqa: E402
from app.domain.agent.chat import ChatService  # noqa: E402
from app.domain.agent.service import (  # noqa: E402
    AgentDelta,
    AgentResult,
    AgentService,
    AgentUsage,
)
from app.main import app  # noqa: E402

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
    finish: they run on the TestClient portal loop and write to this worker's DB —
    if a turn is still writing when the next test truncates, the test flakes.
    Returns as soon as they're idle; the generous ceiling only matters under heavy
    parallel/external load, when a turn can take much longer than usual."""
    runner = get_turn_runner()
    for _ in range(3000):  # ~30s ceiling; returns early the instant turns drain
        if runner.active_turns() == 0:
            return
        time.sleep(0.01)


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
    get_broker().reset()  # channel ids reset with the DB; drop stale buffered frames

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


# --- PostgreSQL test-DB plumbing (per-worker, see the module docstring) --------


async def _truncate_all(engine) -> None:
    """Wipe every table for a clean per-test slate (fast; keeps the schema)."""
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    if not tables:
        return
    async with engine.begin() as conn:
        await conn.exec_driver_sql(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")


def _create_and_migrate(db_name: str, db_url: str) -> None:
    """Drop + recreate a database and migrate it to head (alembic)."""
    import subprocess
    from pathlib import Path

    backend_dir = Path(__file__).resolve().parent.parent
    # Drop + recreate from the maintenance `postgres` DB (can't drop a DB you're
    # connected to); FORCE closes any stale connection. Separate -c flags because
    # DROP/CREATE DATABASE can't run inside a transaction and psql wraps multiple
    # statements in one -c into a single transaction.
    subprocess.run(
        [
            "docker",
            "exec",
            "cheesex-pg",
            "psql",
            "-U",
            "cheesex",
            "-d",
            "postgres",
            "-c",
            f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)',
            "-c",
            f'CREATE DATABASE "{db_name}"',
        ],
        check=True,
        capture_output=True,
    )
    # DATABASE_URL maps to settings.database_url, which alembic/env.py reads.
    subprocess.run(
        [str(backend_dir / ".venv/bin/alembic"), "upgrade", "head"],
        cwd=backend_dir,
        env={**os.environ, "DATABASE_URL": db_url},
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="session", autouse=True)
def _pg_schema():
    """Create + migrate THIS worker's two dedicated databases once per session:
    the integration DB (settings.database_url, bound by the app engines) and the
    client/python_client DB (TEST_DATABASE_URL). autouse so the integration harness
    — which binds to settings.database_url — always finds a ready schema too. Both
    are per-worker, so nothing races across xdist workers. Requires Docker pg :5433.
    """
    _create_and_migrate(_INTG_DB_NAME, settings.database_url)
    _create_and_migrate(_CLIENT_DB_NAME, TEST_DATABASE_URL)
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
    get_broker().reset()  # channel ids reset with the DB; drop stale buffered frames
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

    # The merged app has TWO get_db symbols with their own module-level engines:
    # cheesex routes depend on app.core.db.get_db, but the 知是 routes (spaces,
    # teams, tasks, questions, materials, …) depend on app.db.session.get_db,
    # whose pooled engine is loop-bound. Override BOTH onto the per-worker test
    # factory (NullPool) so every route reads the isolated test DB on the calling
    # loop — otherwise 知是 endpoints hit the real dev engine and crash with
    # "attached to a different loop" once a second event loop touches the pool.
    from app.db.session import get_db as get_db_zhishi

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_db_zhishi] = override_get_db
    app.dependency_overrides[get_chat_service] = override_get_chat_service

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Cheese-Token": SANDBOX_TOKEN},
    ) as c:
        # Expose the per-worker factory so contract tests can seed rows (e.g. a
        # real authenticated user) on the SAME DB the app reads through get_db.
        c.test_factory = test_factory  # type: ignore[attr-defined]
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
