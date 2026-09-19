"""Real-database regression tests for the refresh_token rotation fragility
fix (让降级原因可见 + 修 refresh_token 轮换的脆弱性, 2026-08-09).

案底 (决策 0556ac50): GitHub App user-to-server refresh_tokens are one-shot —
refreshing invalidates the old one. `OAuthService._refresh_and_persist_token`
used to run its update inside whatever transaction the caller (typically
`AcceptService.accept()`) happened to be mid-way through. Two problems
followed: a later, unrelated failure in that SAME transaction rolled the
refreshed token back too (leaving the DB holding a refresh_token GitHub had
already invalidated — the account link then stays broken until the user
manually reconnects), and two concurrent refreshers of the same connection
could race and clobber each other.

The fix (`OAuthService._refresh_and_persist_token`, `app/domain/oauth/
services.py`) commits the refresh on its OWN independent session/transaction.
Racers of one connection in the same process wait on an asyncio lock and
reuse the winner's result instead of calling GitHub again; the write itself
is conditional on the stored refresh_token being the one that was read, so a
racer in another process cannot clobber it either. No database lock is held
while GitHub is called (dev outage of 2026-09-18: a row lock held across a
network call queued every later caller with a pool connection each).

These tests exercise that for real against Postgres — no mocking of the
session/repository layer, only the outbound GitHub HTTP call (a true
external boundary) is faked. A mocked-repo unit test cannot observe this
fix at all: it is exactly the over-mocked kind of test whose blind spot let
the original bug ship (see tests/unit/test_oauth_service.py's
TestGetGithubUserToken note).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.crypto import decrypt_text, encrypt_text
from app.domain.oauth.models import UserOAuthConnection
from app.domain.oauth.repositories import OAuthConnectionRepository
from app.domain.oauth.services import GitHubProvider, OAuthProviderConfig, OAuthService

_USER_ID_COUNTER = 900_000_000


def _next_user_id() -> int:
    global _USER_ID_COUNTER
    _USER_ID_COUNTER += 1
    return _USER_ID_COUNTER


def _github_app_config() -> OAuthProviderConfig:
    return OAuthProviderConfig(
        id="github_app",
        name="GitHub (cheesex-app)",
        client_id="test-client-id",
        client_secret="test-client-secret",
        authorization_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        redirect_url="https://example.com/callback",
        scope=["read:user"],
    )


def _service_with_stub_provider(session) -> tuple[OAuthService, GitHubProvider]:
    """A real OAuthService/repo bound to `session`'s engine, with a stub
    GitHubProvider standing in for the actual GitHub HTTP call (never a
    real network call in tests) — everything else (session handling, the
    row lock, the commit) is the real production code path."""
    svc = OAuthService(repo=OAuthConnectionRepository(session))
    svc._initialized = True
    provider = GitHubProvider(_github_app_config())
    svc._providers = {"github_app": provider}
    return svc, provider


async def _seed_connection(
    session_factory: async_sessionmaker,
    *,
    user_id: int,
    access_token: str = "stale-access-token",
    refresh_token: str | None = "stale-refresh-token",
    expires_in: timedelta = timedelta(hours=-1),
) -> int:
    async with session_factory() as session:
        conn = UserOAuthConnection(
            user_id=user_id,
            provider_id="github_app",
            provider_user_id=f"gh-{user_id}",
            access_token=encrypt_text(access_token),
            refresh_token=encrypt_text(refresh_token) if refresh_token else None,
            token_expires=datetime.now(UTC) + expires_in,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(conn)
        await session.commit()
        await session.refresh(conn)
        return conn.id


async def _read_connection(
    session_factory: async_sessionmaker, connection_id: int
) -> UserOAuthConnection:
    async with session_factory() as session:
        result = await session.execute(
            select(UserOAuthConnection).where(UserOAuthConnection.id == connection_id)
        )
        return result.scalar_one()


def _decrypted(value: str | None) -> str:
    assert value is not None
    return decrypt_text(value)


@pytest.mark.anyio
async def test_refresh_survives_outer_transaction_rollback(client):
    """The key regression assertion: refresh succeeds, then a LATER step in
    the SAME caller transaction fails and the whole thing rolls back — the
    refreshed token must still be the one sitting in the database afterward,
    not the stale value GitHub has already invalidated."""
    factory = client.test_factory
    user_id = _next_user_id()
    connection_id = await _seed_connection(factory, user_id=user_id)

    async with factory() as outer_session:
        svc, provider = _service_with_stub_provider(outer_session)
        provider.refresh_access_token = AsyncMock(
            return_value={
                "access_token": "fresh-access-token",
                "expires_in": 28800,
                "refresh_token": "fresh-refresh-token",
            }
        )

        token = await svc.get_github_user_token(user_id, provider_id="github_app")
        assert token == "fresh-access-token"

        # Simulate accept()'s next step blowing up (e.g. the PR-open call
        # failing) — the caller's own session/transaction rolls back.
        await outer_session.rollback()

    # A brand new session/connection: proves the refresh was committed to
    # the database for real, independent of the caller's rollback above.
    conn = await _read_connection(factory, connection_id)
    assert _decrypted(conn.access_token) == "fresh-access-token"
    assert _decrypted(conn.refresh_token) == "fresh-refresh-token"
    assert conn.token_expires is not None
    assert conn.token_expires > datetime.now(UTC) + timedelta(hours=7)


@pytest.mark.anyio
async def test_refresh_http_failure_leaves_stale_token_untouched(client):
    factory = client.test_factory
    user_id = _next_user_id()
    connection_id = await _seed_connection(factory, user_id=user_id)

    async with factory() as session:
        svc, provider = _service_with_stub_provider(session)
        provider.refresh_access_token = AsyncMock(side_effect=Exception("github 500"))

        token = await svc.get_github_user_token(user_id, provider_id="github_app")
        assert token is None

    conn = await _read_connection(factory, connection_id)
    assert _decrypted(conn.access_token) == "stale-access-token"
    assert _decrypted(conn.refresh_token) == "stale-refresh-token"


@pytest.mark.anyio
async def test_undecryptable_stored_refresh_token_degrades_without_writing(client):
    """A refresh_token column that isn't valid Fernet ciphertext (key
    rotated, or a stray legacy row) must degrade to None, not raise — and
    must not touch the row it can't safely act on."""
    factory = client.test_factory
    user_id = _next_user_id()
    async with factory() as session:
        conn = UserOAuthConnection(
            user_id=user_id,
            provider_id="github_app",
            provider_user_id=f"gh-{user_id}",
            access_token=encrypt_text("stale-access-token"),
            refresh_token="not-a-fernet-token",
            token_expires=datetime.now(UTC) - timedelta(hours=1),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(conn)
        await session.commit()
        await session.refresh(conn)
        connection_id = conn.id

    async with factory() as session:
        svc, provider = _service_with_stub_provider(session)
        provider.refresh_access_token = AsyncMock()

        token = await svc.get_github_user_token(user_id, provider_id="github_app")
        assert token is None

    provider.refresh_access_token.assert_not_awaited()
    conn = await _read_connection(factory, connection_id)
    assert _decrypted(conn.access_token) == "stale-access-token"


@pytest.mark.anyio
async def test_within_margin_token_is_refreshed_early(client):
    """Inside the refresh margin counts as expired, not just past expiry."""
    factory = client.test_factory
    user_id = _next_user_id()
    connection_id = await _seed_connection(
        factory, user_id=user_id, expires_in=timedelta(minutes=1)
    )

    async with factory() as session:
        svc, provider = _service_with_stub_provider(session)
        provider.refresh_access_token = AsyncMock(
            return_value={"access_token": "fresh-access-token", "expires_in": 28800}
        )

        token = await svc.get_github_user_token(user_id, provider_id="github_app")

    assert token == "fresh-access-token"
    conn = await _read_connection(factory, connection_id)
    assert _decrypted(conn.access_token) == "fresh-access-token"


@pytest.mark.anyio
async def test_concurrent_refresh_serializes_and_calls_github_once(client):
    """Two callers racing to refresh the SAME about-to-expire connection:
    only ONE actually calls GitHub — the loser waits, then reuses the
    winner's fresh token."""
    factory = client.test_factory
    user_id = _next_user_id()
    connection_id = await _seed_connection(factory, user_id=user_id)

    call_count = 0

    async def fake_refresh(refresh_token: str) -> dict:
        nonlocal call_count
        call_count += 1
        # Stay in flight for a bit so both racers genuinely overlap, not
        # accidentally serialized by asyncio itself.
        await asyncio.sleep(0.05)
        return {
            "access_token": "fresh-access-token",
            "expires_in": 28800,
            "refresh_token": "fresh-refresh-token",
        }

    async def run_one() -> str | None:
        async with factory() as session:
            svc, provider = _service_with_stub_provider(session)
            provider.refresh_access_token = fake_refresh
            return await svc.get_github_user_token(user_id, provider_id="github_app")

    results = await asyncio.gather(run_one(), run_one())

    assert call_count == 1, "the loser must reuse the winner's token, not refresh again"
    assert results[0] == results[1] == "fresh-access-token"
    conn = await _read_connection(factory, connection_id)
    assert _decrypted(conn.access_token) == "fresh-access-token"


@pytest.mark.anyio
async def test_refresh_holds_no_row_lock_while_github_is_in_flight(client):
    """While the GitHub call is in flight, the connection row stays free: a
    writer elsewhere locks it at once instead of queueing behind the refresh
    (dev outage of 2026-09-18, same shape on another table)."""
    from sqlalchemy import text

    factory = client.test_factory
    user_id = _next_user_id()
    connection_id = await _seed_connection(factory, user_id=user_id)
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def fake_refresh(refresh_token: str) -> dict:
        in_flight.set()
        await release.wait()
        return {
            "access_token": "fresh-access-token",
            "expires_in": 28800,
            "refresh_token": "fresh-refresh-token",
        }

    async def run_refresh() -> str | None:
        async with factory() as session:
            svc, provider = _service_with_stub_provider(session)
            provider.refresh_access_token = fake_refresh
            return await svc.get_github_user_token(user_id, provider_id="github_app")

    refresh = asyncio.create_task(run_refresh())
    await asyncio.wait_for(in_flight.wait(), timeout=5)
    async with factory() as session:
        # NOWAIT fails immediately if the refresh were holding the row.
        locked = await session.scalar(
            text(
                "SELECT id FROM user_o_auth_connection WHERE id = :id FOR UPDATE NOWAIT"
            ),
            {"id": connection_id},
        )
        await session.rollback()
    assert locked == connection_id
    release.set()
    assert await asyncio.wait_for(refresh, timeout=5) == "fresh-access-token"


@pytest.mark.anyio
async def test_refresh_that_lands_second_keeps_the_first_writers_tokens(client):
    """A refresher in another process rotates the tokens while ours is
    waiting on GitHub. Ours must not overwrite them with a result GitHub
    has since invalidated; the caller gets the live token instead."""
    factory = client.test_factory
    user_id = _next_user_id()
    connection_id = await _seed_connection(factory, user_id=user_id)
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def fake_refresh(refresh_token: str) -> dict:
        in_flight.set()
        await release.wait()
        return {
            "access_token": "late-access-token",
            "expires_in": 28800,
            "refresh_token": "late-refresh-token",
        }

    async def run_refresh() -> str | None:
        async with factory() as session:
            svc, provider = _service_with_stub_provider(session)
            provider.refresh_access_token = fake_refresh
            return await svc.get_github_user_token(user_id, provider_id="github_app")

    refresh = asyncio.create_task(run_refresh())
    await asyncio.wait_for(in_flight.wait(), timeout=5)
    async with factory() as session:
        # Another process's refresh: it read the same stored token, won at
        # GitHub, and committed. Direct SQL because the in-process lock would
        # make a second service call wait rather than race.
        other = await OAuthConnectionRepository(session).get(connection_id)
        assert other is not None
        other.access_token = encrypt_text("winner-access-token")
        other.refresh_token = encrypt_text("winner-refresh-token")
        other.token_expires = datetime.now(UTC) + timedelta(hours=8)
        await session.commit()
    release.set()
    assert await asyncio.wait_for(refresh, timeout=5) == "winner-access-token"
    conn = await _read_connection(factory, connection_id)
    assert _decrypted(conn.access_token) == "winner-access-token"
    assert _decrypted(conn.refresh_token) == "winner-refresh-token"
