"""Integration test fixtures.

This conftest runs the FastAPI app in-process via ``TestClient`` and isolates
each test in a SAVEPOINT-rolled-back transaction.

Important loop-affinity invariant: every piece of async work (TestClient
lifespan, request handling, ORM writes done via ``UserCreator``, opening the
shared DB connection, etc.) must run on the *same* event loop. We achieve
this by creating one session-scoped anyio blocking portal and:

* using ``_portal`` to drive all async DB operations from sync test code, and
* assigning the same portal to ``TestClient.portal`` so the test client
  reuses it instead of spinning up a fresh per-instance loop.
"""

import uuid
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import bcrypt
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
)

from app.core.tokens import mint_session_token

if TYPE_CHECKING:
    from anyio.from_thread import BlockingPortal
    from fastapi import FastAPI
    from fastapi.testclient import TestClient


def room_text(blocks: list[dict]) -> str:
    """Everything the room says, `content` **and** folded-away `meta.detail`.

    平台提示统一契约: a platform notice is one line of `content` plus a
    structured `meta`, and anything long (the CI log, the check output, the
    provider's own words) rides in `meta.detail` for the frontend to fold. So
    a test asking "did the platform say X" has to read both halves — joining
    `content` alone now answers "is X on the visible line", which is a
    different and much narrower question.

    Use this for「说了什么」assertions. When you specifically mean "the visible
    line", read `content` directly and say so.
    """
    parts: list[str] = []
    for block in blocks:
        parts.append(block.get("content") or "")
        detail = (block.get("meta") or {}).get("detail")
        if detail:
            parts.append(str(detail))
    return "\n".join(parts)


def unique_int(min_val: int = 10000000, max_val: int = 99999999) -> int:
    """Collision-resistant random int derived from a fresh UUID4.

    Use this instead of random.randint() in integration tests to avoid
    suffix collisions when many tests run against a shared database.
    """
    span = max_val - min_val + 1
    return min_val + (uuid.uuid4().int % span)


def session_token(handle: str, *, ttl_s: int | None = None) -> str:
    """A handle-scoped session token — the ONLY sanctioned way for a test to
    mint one.

    No DB user is created: the token carries ``sub=<handle>`` and no numeric
    user id, which is exactly what handle-based actor resolution (accept /
    split / connector / project routes) keys off. When a test needs a real DB
    user instead, use ``seed_user`` or the ``authenticated_user`` fixture.

    ``ttl_s`` overrides the lifetime; pass a negative value for an already-expired
    token (what a client holds after leaving a tab open over the weekend).

    ``backend/tests/`` must not import ``mint_session_token`` directly —
    ``tests/unit/test_no_adhoc_auth_helpers.py`` enforces it. Fourteen
    hand-rolled copies of this had accumulated — 9 named ``_auth``, 3 named
    ``_login``, 2 inlined into headers — which is why the rule is now a test
    rather than a sentence in a rules file.
    """
    return mint_session_token(handle=handle, user_id=None, ttl_s=ttl_s)


def session_auth_headers(handle: str) -> dict[str, str]:
    """``Authorization`` header carrying :func:`session_token` for ``handle``."""
    return {"Authorization": f"Bearer {session_token(handle)}"}


def chat_ws_url(topic_id: str, handle: str) -> str:
    """The topic's chat WebSocket, authenticated as ``handle``.

    The socket requires a session token (``app.api.routes.chat``), so tests take
    the same path the browser does. ``handle`` must be able to reach the topic —
    its roster owner, or a member/owner of its project — or the connect is
    refused with ``code: forbidden``.
    """
    return f"/topics/{topic_id}/chat?token={session_token(handle)}"


@dataclass
class CreatedUser:
    user_id: int
    username: str
    password: str
    email: str
    nickname: str
    avatar_id: int
    intro: str
    token: str | None = None


class UserCreator:
    """Creates users via direct ORM writes that share the test transaction.

    All async work is dispatched to the session-scoped anyio portal so that
    the writes happen on the same event loop as the FastAPI app and inside
    the per-test SAVEPOINT.
    """

    def __init__(self, db_session: AsyncSession, portal: "BlockingPortal") -> None:
        self._db = db_session
        self._portal = portal

    def _test_username(self) -> str:
        return f"PyTestUser-{uuid.uuid4().hex[:16]}"

    def _test_password(self) -> str:
        return "abc123456Test"

    def _test_email(self) -> str:
        return f"pytest-{uuid.uuid4().hex[:16]}@ruc.edu.cn"

    def _test_nickname(self) -> str:
        return f"pytest_user_{uuid.uuid4().hex[:8]}"

    def _test_avatar_id(self) -> int:
        return 1

    def _test_intro(self) -> str:
        return "This user has not set an introduction yet."

    async def _do_insert(
        self,
        username: str,
        email: str,
        hashed_password: str,
        nickname: str,
        intro: str,
        avatar_id: int,
    ) -> int:
        from app.domain.user.models import User, UserProfile

        now = datetime.now(UTC)
        user = User(
            username=username,
            email=email,
            hashed_password=hashed_password,
            created_at=now,
            updated_at=now,
        )
        self._db.add(user)
        await self._db.flush()
        user_id = user.id

        profile = UserProfile(
            user_id=user_id,
            nickname=nickname,
            intro=intro,
            avatar_id=avatar_id,
            created_at=now,
            updated_at=now,
        )
        self._db.add(profile)
        await self._db.flush()
        return user_id

    def create_user(
        self,
        username: str | None = None,
        password: str | None = None,
        email: str | None = None,
        nickname: str | None = None,
        avatar_id: int | None = None,
        intro: str | None = None,
    ) -> CreatedUser:
        username = username or self._test_username()
        password = password or self._test_password()
        email = email or self._test_email()
        nickname = nickname or self._test_nickname()
        avatar_id = avatar_id or self._test_avatar_id()
        intro = intro or self._test_intro()

        # rounds=4 in tests (vs default 12) saves ~300ms per user creation.
        # Tests don't need brute-force resistance.
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()

        async def _coro() -> int:
            return await self._do_insert(
                username=username,
                email=email,
                hashed_password=hashed,
                nickname=nickname,
                intro=intro,
                avatar_id=avatar_id,
            )

        user_id = self._portal.call(_coro)

        return CreatedUser(
            user_id=user_id,
            username=username,
            password=password,
            email=email,
            nickname=nickname,
            avatar_id=avatar_id,
            intro=intro,
        )

    def login(self, client: "TestClient", username: str, password: str) -> str:
        response = client.post(
            "/users/auth/login",
            json={"username": username, "password": password},
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        return data["data"]["accessToken"]


# ---------------------------------------------------------------------------
# Core fixtures: app, portal, transactional db_session, in-process api_client
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def app() -> "FastAPI":
    """Import the FastAPI app. (Its periodic jobs only start inside `lifespan`,
    which these tests do not enter — nothing to patch out here.)"""
    from app.main import app as fastapi_app

    return fastapi_app


@pytest.fixture(scope="session")
def _portal() -> Generator["BlockingPortal"]:
    """Session-wide anyio blocking portal so sync test code can call async ORM
    on the same event loop as the FastAPI TestClient.
    """
    from anyio.from_thread import start_blocking_portal

    with start_blocking_portal(backend="asyncio") as portal:
        yield portal


@pytest.fixture(scope="session")
def db_connection(_pg_schema, _portal: "BlockingPortal") -> Generator[AsyncConnection]:
    """A single PG connection shared by every test in the session.

    Opened on the session portal's loop so that every per-test transaction
    nested inside it runs on the same loop as the TestClient. Also seeds any
    reference data that the app would otherwise lazily initialize on first
    request (e.g. default discussion reaction types) — those would otherwise
    be wiped by the per-test rollback while their sequence-allocated ids
    advance, breaking tests that hard-code id=1.
    """
    from app.db.session import engine

    async def _open() -> AsyncConnection:
        return await engine.connect()

    async def _close(conn: AsyncConnection) -> None:
        await conn.close()

    async def _seed_reference_data(conn: AsyncConnection) -> None:
        # Seed default discussion reaction types with deterministic ids
        # (1..N). Tests reference id=1 directly. We use INSERT ... ON
        # CONFLICT DO NOTHING so re-running the suite is idempotent, and we
        # advance the sequence past the seeded ids so that future inserts
        # via Sequence.next_value() do not collide.
        from sqlalchemy import text as _text

        defaults = [
            (1, "LIKE", "Like", "thumbs up", 0),
            (2, "CHEERS", "Cheers", "celebration", 1),
            (3, "HEART", "Heart", "love", 2),
            (4, "INSIGHTFUL", "Insightful", "thoughtful", 3),
            (5, "DISLIKE", "Dislike", "thumbs down", 4),
        ]
        for rid, code, name, desc, order in defaults:
            await conn.execute(
                _text(
                    "INSERT INTO reaction_type (id, code, name, description, "
                    "display_order, is_active, created_at, updated_at) "
                    "VALUES (:id, :code, :name, :desc, :ord, TRUE, NOW(), NOW()) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"id": rid, "code": code, "name": name, "desc": desc, "ord": order},
            )
        # Make sure subsequent serial allocations skip past the seeded ids.
        await conn.execute(
            _text(
                "SELECT setval('reaction_type_seq', GREATEST(last_value, 100), TRUE) "
                "FROM reaction_type_seq"
            )
        )

    conn = _portal.call(_open)
    try:
        # Seed reference data inside an outer transaction we COMMIT so that
        # the rows survive every per-test SAVEPOINT rollback.
        outer = _portal.call(conn.begin)
        try:
            _portal.call(_seed_reference_data, conn)
            _portal.call(outer.commit)
        except BaseException:
            _portal.call(outer.rollback)
            raise
        yield conn
    finally:
        _portal.call(_close, conn)


@pytest.fixture
def db_session(
    db_connection: AsyncConnection,
    _portal: "BlockingPortal",
) -> Generator[AsyncSession]:
    """Per-test AsyncSession nested in a SAVEPOINT inside an outer transaction.

    With ``join_transaction_mode="create_savepoint"``, calls to
    ``session.commit()`` from within app code release the SAVEPOINT instead of
    committing the outer transaction. After the test, we roll back the outer
    transaction and the database returns to its pre-test state.
    """
    SessionLocal = async_sessionmaker(
        bind=db_connection,
        expire_on_commit=False,
        class_=AsyncSession,
        join_transaction_mode="create_savepoint",
    )

    state: dict[str, object] = {}

    async def _begin() -> None:
        state["outer"] = await db_connection.begin()
        state["session"] = SessionLocal()

    async def _end() -> None:
        session = state.get("session")
        if isinstance(session, AsyncSession):
            await session.close()
        outer = state.get("outer")
        if outer is not None and getattr(outer, "is_active", False):
            await outer.rollback()  # type: ignore[union-attr]

    _portal.call(_begin)
    try:
        session = state["session"]
        assert isinstance(session, AsyncSession)
        yield session
    finally:
        _portal.call(_end)


@pytest.fixture
def api_client(
    app: "FastAPI",
    db_session: AsyncSession,
    _portal: "BlockingPortal",
) -> Generator["TestClient"]:
    """In-process FastAPI TestClient with ``get_db`` overridden to share the
    per-test transactional session. The TestClient is forced to use the
    session-wide portal so all async work runs on a single event loop.
    """
    from collections.abc import AsyncGenerator

    from fastapi.testclient import TestClient

    from app.db.session import get_db

    async def _get_test_db() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _get_test_db
    client = TestClient(app, base_url="http://testserver")
    # Pin TestClient to our session portal so request handling shares the
    # same event loop as our ORM writes / the shared DB connection. We
    # deliberately skip ``with client:`` because it would replace our portal
    # with a fresh one for every test and run lifespan startup/shutdown
    # repeatedly — which would also start every periodic job the platform runs
    # (scheduler/jobs.py), once per test.
    client.portal = _portal  # type: ignore[assignment]
    try:
        yield client
    finally:
        client.close()
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def user_client(db_session: AsyncSession, _portal: "BlockingPortal") -> UserCreator:
    return UserCreator(db_session, _portal)


@pytest.fixture
def authenticated_user(
    user_client: UserCreator,
    api_client: "TestClient",
) -> CreatedUser:
    user = user_client.create_user()
    token = user_client.login(api_client, user.username, user.password)
    user.token = token
    return user


@pytest.fixture
def auth_headers(authenticated_user: CreatedUser) -> dict[str, str]:
    return {"Authorization": f"Bearer {authenticated_user.token}"}


@pytest.fixture
def github_binding_user(client, monkeypatch):
    """A real project caller with a stubbed, already-linked GitHub App token."""
    from app.domain.oauth.services import OAuthService
    from tests.conftest import seed_user

    seed_user(client, "alice")

    async def token(self, user_id):
        return "test-github-user-token"

    monkeypatch.setattr(OAuthService, "get_github_user_token", token)
