"""Unit tests for app.domain.oauth.repositories.OAuthConnectionRepository."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.oauth.repositories import OAuthConnectionRepository

NOW = datetime(2025, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _connection(**overrides):
    defaults = {
        "id": 1,
        "user_id": 10,
        "provider_id": "github",
        "provider_user_id": "gh_123",
        "raw_profile": None,
        "refresh_token": None,
        "token_expires": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    return session


def _mock_scalar(val):
    m = MagicMock()
    m.scalar_one_or_none.return_value = val
    return m


def _mock_scalars(lst):
    m = MagicMock()
    s = MagicMock()
    s.all.return_value = lst
    m.scalars.return_value = s
    return m


def _mock_rowcount(count):
    m = MagicMock()
    m.rowcount = count
    return m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOAuthConnectionRepository:
    @pytest.mark.anyio
    async def test_create(self):
        session = _mock_session()
        repo = OAuthConnectionRepository(session)

        result = await repo.create(user_id=10, provider_id="github", provider_user_id="gh_123")
        assert result.user_id == 10
        assert result.provider_id == "github"
        assert result.provider_user_id == "gh_123"
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_create_with_optional_fields(self):
        session = _mock_session()
        repo = OAuthConnectionRepository(session)
        expires = datetime(2025, 12, 31, tzinfo=UTC)

        result = await repo.create(
            user_id=10,
            provider_id="github",
            provider_user_id="gh_123",
            raw_profile={"login": "user"},
            refresh_token="refresh_token",
            token_expires=expires,
        )
        assert result.raw_profile == {"login": "user"}
        assert result.refresh_token == "refresh_token"

    @pytest.mark.anyio
    async def test_get_by_provider_found(self):
        session = _mock_session()
        conn = _connection()
        session.execute.return_value = _mock_scalar(conn)
        repo = OAuthConnectionRepository(session)

        result = await repo.get_by_provider("github", "gh_123")
        assert result is conn

    @pytest.mark.anyio
    async def test_get_by_provider_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = OAuthConnectionRepository(session)

        assert await repo.get_by_provider("github", "nonexistent") is None

    @pytest.mark.anyio
    async def test_get_by_user_and_provider(self):
        session = _mock_session()
        conn = _connection()
        session.execute.return_value = _mock_scalar(conn)
        repo = OAuthConnectionRepository(session)

        result = await repo.get_by_user_and_provider(10, "github")
        assert result is conn

    @pytest.mark.anyio
    async def test_list_by_user(self):
        session = _mock_session()
        conn = _connection()
        session.execute.return_value = _mock_scalars([conn])
        repo = OAuthConnectionRepository(session)

        result = await repo.list_by_user(10)
        assert result == [conn]

    @pytest.mark.anyio
    async def test_delete_by_id_success(self):
        session = _mock_session()
        session.execute.return_value = _mock_rowcount(1)
        repo = OAuthConnectionRepository(session)

        result = await repo.delete_by_id(1, 10)
        assert result is True

    @pytest.mark.anyio
    async def test_delete_by_id_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_rowcount(0)
        repo = OAuthConnectionRepository(session)

        result = await repo.delete_by_id(999, 10)
        assert result is False

    @pytest.mark.anyio
    async def test_update_tokens_found(self):
        session = _mock_session()
        conn = _connection()
        session.execute.return_value = _mock_scalar(conn)
        repo = OAuthConnectionRepository(session)

        expires = datetime(2025, 12, 31, tzinfo=UTC)
        await repo.update_tokens(1, "new_refresh", expires)
        assert conn.refresh_token == "new_refresh"
        assert conn.token_expires == expires
        session.flush.assert_awaited_once()

    @pytest.mark.anyio
    async def test_update_tokens_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = OAuthConnectionRepository(session)

        await repo.update_tokens(999, "new_refresh", None)
        session.flush.assert_not_awaited()
