"""Unit tests for app.domain.llm.repositories.

Covers AIUserQuotaRepository, AIConversationRepository, AIMessageRepository.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.llm.repositories import (
    AIConversationRepository,
    AIMessageRepository,
    AIUserQuotaRepository,
)

# Collection and execution can straddle UTC midnight in the full CI suite.
NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    clock = MagicMock(wraps=datetime)
    clock.now.return_value = NOW
    monkeypatch.setattr("app.domain.llm.repositories.datetime", clock)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _quota(**overrides):
    defaults = {
        "id": 1,
        "user_id": 10,
        "daily_seu_quota": 100.0,
        "remaining_seu": 50.0,
        "total_seu_consumed": 50.0,
        "last_reset_time": NOW,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _conversation(**overrides):
    defaults = {
        "id": 1,
        "owner_id": 10,
        "conversation_id": "uuid-123",
        "title": "Test Chat",
        "model_type": "standard",
        "module_type": "GENERAL",
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
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


def _mock_scalar_val(val):
    """For result.scalar() calls (not scalar_one)."""
    m = MagicMock()
    m.scalar.return_value = val
    return m


def _mock_scalars(lst):
    m = MagicMock()
    s = MagicMock()
    s.all.return_value = lst
    m.scalars.return_value = s
    return m


# ---------------------------------------------------------------------------
# AIUserQuotaRepository
# ---------------------------------------------------------------------------


class TestAIUserQuotaRepository:
    @pytest.mark.anyio
    async def test_get_or_create_creates_new(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = AIUserQuotaRepository(session)

        result = await repo.get_or_create(10, 100.0)
        assert result.user_id == 10
        assert result.daily_seu_quota == 100.0
        assert result.remaining_seu == 100.0
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_get_or_create_returns_existing(self):
        session = _mock_session()
        existing = _quota(last_reset_time=NOW)
        session.execute.return_value = _mock_scalar(existing)
        repo = AIUserQuotaRepository(session)

        result = await repo.get_or_create(10, 100.0)
        assert result is existing

    @pytest.mark.anyio
    async def test_get_or_create_resets_on_new_day(self):
        session = _mock_session()
        yesterday = NOW - timedelta(days=1)
        existing = _quota(
            last_reset_time=yesterday,
            remaining_seu=10.0,
            total_seu_consumed=90.0,
        )
        session.execute.return_value = _mock_scalar(existing)
        repo = AIUserQuotaRepository(session)

        result = await repo.get_or_create(10, 100.0)
        assert result.remaining_seu == 100.0
        assert result.total_seu_consumed == 0.0

    @pytest.mark.anyio
    async def test_consume_success(self):
        session = _mock_session()
        existing = _quota(remaining_seu=50.0, total_seu_consumed=50.0)
        session.execute.return_value = _mock_scalar(existing)
        repo = AIUserQuotaRepository(session)

        remaining, reset_at, total = await repo.consume(10, 10.0, 10.0)
        assert remaining == 40.0
        assert total == 100.0
        assert existing.total_seu_consumed == 60.0
        assert reset_at > NOW
        assert (reset_at.hour, reset_at.minute, reset_at.second) == (0, 0, 0)

    @pytest.mark.anyio
    async def test_consume_exhausted(self):
        session = _mock_session()
        existing = _quota(remaining_seu=5.0)
        session.execute.return_value = _mock_scalar(existing)
        repo = AIUserQuotaRepository(session)

        with pytest.raises(ValueError, match="quota exhausted"):
            await repo.consume(10, 10.0, 100.0)

    @pytest.mark.anyio
    async def test_get_quota(self):
        session = _mock_session()
        existing = _quota(remaining_seu=75.0)
        session.execute.return_value = _mock_scalar(existing)
        repo = AIUserQuotaRepository(session)

        remaining, reset_at, total = await repo.get_quota(10, 10.0)
        assert remaining == 75.0
        assert total == 100.0


# ---------------------------------------------------------------------------
# AIConversationRepository
# ---------------------------------------------------------------------------


class TestAIConversationRepository:
    @pytest.mark.anyio
    async def test_create(self):
        session = _mock_session()
        repo = AIConversationRepository(session)

        result = await repo.create(user_id=10, title="Chat")
        assert result.owner_id == 10
        assert result.title == "Chat"
        assert result.conversation_id is not None
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_get_by_id_found(self):
        session = _mock_session()
        convo = _conversation()
        session.execute.return_value = _mock_scalar(convo)
        repo = AIConversationRepository(session)

        result = await repo.get_by_id(1)
        assert result is convo

    @pytest.mark.anyio
    async def test_get_by_id_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = AIConversationRepository(session)

        assert await repo.get_by_id(999) is None

    @pytest.mark.anyio
    async def test_list_by_user_no_results(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = AIConversationRepository(session)

        rows, page_info = await repo.list_by_user(10)
        assert rows == []
        assert page_info["pageStart"] is None
        assert page_info["hasMore"] is False

    @pytest.mark.anyio
    async def test_list_by_user_with_results(self):
        session = _mock_session()
        c1 = _conversation(id=10)
        c2 = _conversation(id=9)
        session.execute.return_value = _mock_scalars([c1, c2])
        repo = AIConversationRepository(session)

        rows, page_info = await repo.list_by_user(10, page_size=20)
        assert rows == [c1, c2]
        assert page_info["pageStart"] == 10
        assert page_info["hasMore"] is False

    @pytest.mark.anyio
    async def test_list_by_user_has_more(self):
        session = _mock_session()
        # page_size=2, return 3 items -> has_more=True
        convos = [_conversation(id=10), _conversation(id=9), _conversation(id=8)]
        session.execute.return_value = _mock_scalars(convos)
        repo = AIConversationRepository(session)

        rows, page_info = await repo.list_by_user(10, page_size=2)
        assert len(rows) == 2
        assert page_info["hasMore"] is True
        assert page_info["nextStart"] == 9  # last item of trimmed list

    @pytest.mark.anyio
    async def test_list_by_user_with_page_start(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = AIConversationRepository(session)

        rows, _ = await repo.list_by_user(10, page_start=50, page_size=10)
        assert rows == []

    @pytest.mark.anyio
    async def test_delete_found(self):
        session = _mock_session()
        convo = _conversation()
        session.execute.return_value = _mock_scalar(convo)
        repo = AIConversationRepository(session)

        result = await repo.delete(1)
        assert result is True
        assert convo.deleted_at is not None

    @pytest.mark.anyio
    async def test_delete_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = AIConversationRepository(session)

        result = await repo.delete(999)
        assert result is False

    @pytest.mark.anyio
    async def test_update_title_found(self):
        session = _mock_session()
        convo = _conversation(title="Old")
        session.execute.return_value = _mock_scalar(convo)
        repo = AIConversationRepository(session)

        result = await repo.update_title(1, "New Title")
        assert result.title == "New Title"

    @pytest.mark.anyio
    async def test_update_title_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = AIConversationRepository(session)

        assert await repo.update_title(999, "Title") is None


# ---------------------------------------------------------------------------
# AIMessageRepository
# ---------------------------------------------------------------------------


class TestAIMessageRepository:
    @pytest.mark.anyio
    async def test_create(self):
        session = _mock_session()
        repo = AIMessageRepository(session)

        result = await repo.create(conversation_id=1, role="user", content="Hello")
        assert result.conversation_id == 1
        assert result.role == "user"
        assert result.content == "Hello"
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_list_by_conversation(self):
        session = _mock_session()
        msg = SimpleNamespace(id=1, conversation_id=1, role="user")
        session.execute.return_value = _mock_scalars([msg])
        repo = AIMessageRepository(session)

        result = await repo.list_by_conversation(1)
        assert result == [msg]

    @pytest.mark.anyio
    async def test_count_by_conversation(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_val(5)
        repo = AIMessageRepository(session)

        assert await repo.count_by_conversation(1) == 5

    @pytest.mark.anyio
    async def test_count_by_conversation_none(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_val(None)
        repo = AIMessageRepository(session)

        assert await repo.count_by_conversation(1) == 0
