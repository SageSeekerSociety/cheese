"""Unit tests for AiAdviceService (services.py)."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.llm.services import AiAdviceService, QuotaExceededError, QuotaInfo

NOW = datetime(2025, 6, 1, 12, 0, 0)
NOW_MS = int(NOW.replace(tzinfo=UTC).timestamp() * 1000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conversation(**overrides):
    defaults = {
        "id": 1,
        "conversation_id": "uuid-abc-123",
        "owner_id": 42,
        "title": "Test conversation",
        "model_type": "standard",
        "module_type": "GENERAL",
        "created_at": NOW,
        "updated_at": NOW,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_message(**overrides):
    defaults = {
        "id": 100,
        "conversation_id": 1,
        "role": "assistant",
        "content": "Hello there!",
        "tokens_used": 50,
        "created_at": NOW,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _build_advice_service(repo=None, daily_quota=10.0):
    repo = repo or AsyncMock()
    return AiAdviceService(repo=repo, daily_quota=daily_quota)


# ===========================================================================
# AiAdviceService tests
# ===========================================================================


class TestAiAdviceServiceInit:
    def test_init_with_explicit_quota(self):
        repo = AsyncMock()
        svc = AiAdviceService(repo=repo, daily_quota=25.0)
        assert svc._daily_quota == 25.0

    def test_init_defaults_to_settings_quota(self):
        repo = AsyncMock()
        with patch("app.domain.llm.services.settings") as mock_settings:
            mock_settings.ai_daily_quota = 99.0
            svc = AiAdviceService(repo=repo)
            assert svc._daily_quota == 99.0


class TestGetQuota:
    @pytest.mark.anyio
    async def test_get_quota_returns_quota_info(self):
        repo = AsyncMock()
        repo.get_quota.return_value = (7.5, NOW, 10.0)
        svc = _build_advice_service(repo=repo, daily_quota=10.0)

        result = await svc.get_quota(user_id=1)

        assert isinstance(result, QuotaInfo)
        assert result.remaining == 7.5
        assert result.total == 10.0
        assert result.reset_time == NOW
        repo.get_quota.assert_awaited_once_with(user_id=1, daily_total=10.0)


class TestCheckQuota:
    @pytest.mark.anyio
    async def test_check_quota_sufficient(self):
        repo = AsyncMock()
        repo.get_quota.return_value = (5.0, NOW, 10.0)
        svc = _build_advice_service(repo=repo, daily_quota=10.0)

        result = await svc.check_quota(user_id=1, amount=3.0)
        assert result is True

    @pytest.mark.anyio
    async def test_check_quota_exact_boundary(self):
        repo = AsyncMock()
        repo.get_quota.return_value = (3.0, NOW, 10.0)
        svc = _build_advice_service(repo=repo, daily_quota=10.0)

        result = await svc.check_quota(user_id=1, amount=3.0)
        assert result is True

    @pytest.mark.anyio
    async def test_check_quota_insufficient(self):
        repo = AsyncMock()
        repo.get_quota.return_value = (2.0, NOW, 10.0)
        svc = _build_advice_service(repo=repo, daily_quota=10.0)

        result = await svc.check_quota(user_id=1, amount=3.0)
        assert result is False

    @pytest.mark.anyio
    async def test_check_quota_default_amount(self):
        repo = AsyncMock()
        repo.get_quota.return_value = (1.0, NOW, 10.0)
        svc = _build_advice_service(repo=repo, daily_quota=10.0)

        result = await svc.check_quota(user_id=1)
        assert result is True


class TestConsumeQuota:
    @pytest.mark.anyio
    async def test_consume_quota_success(self):
        repo = AsyncMock()
        repo.consume.return_value = (7.0, NOW, 10.0)
        svc = _build_advice_service(repo=repo, daily_quota=10.0)

        result = await svc.consume_quota(user_id=1, amount=3.0)

        assert isinstance(result, QuotaInfo)
        assert result.remaining == 7.0
        assert result.total == 10.0
        assert result.reset_time == NOW
        repo.consume.assert_awaited_once_with(user_id=1, amount=3.0, daily_total=10.0)

    @pytest.mark.anyio
    async def test_consume_quota_negative_remaining_raises(self):
        repo = AsyncMock()
        repo.consume.return_value = (-1.0, NOW, 10.0)
        svc = _build_advice_service(repo=repo, daily_quota=10.0)

        with pytest.raises(QuotaExceededError, match="AI quota exhausted"):
            await svc.consume_quota(user_id=1, amount=11.0)

    @pytest.mark.anyio
    async def test_consume_quota_value_error_raises_quota_exceeded(self):
        repo = AsyncMock()
        repo.consume.side_effect = ValueError("AI quota exhausted")
        svc = _build_advice_service(repo=repo, daily_quota=10.0)

        with pytest.raises(QuotaExceededError, match="AI quota exhausted"):
            await svc.consume_quota(user_id=1, amount=100.0)

    @pytest.mark.anyio
    async def test_consume_quota_default_amount(self):
        repo = AsyncMock()
        repo.consume.return_value = (9.0, NOW, 10.0)
        svc = _build_advice_service(repo=repo, daily_quota=10.0)

        result = await svc.consume_quota(user_id=1)

        assert result.remaining == 9.0
        repo.consume.assert_awaited_once_with(user_id=1, amount=1.0, daily_total=10.0)


class TestConsumeTokens:
    @pytest.mark.anyio
    async def test_consume_tokens_converts_to_seu(self):
        repo = AsyncMock()
        repo.consume.return_value = (8.0, NOW, 10.0)
        svc = _build_advice_service(repo=repo, daily_quota=10.0)

        result = await svc.consume_tokens(user_id=1, tokens=2000)

        assert result.remaining == 8.0
        repo.consume.assert_awaited_once_with(user_id=1, amount=2.0, daily_total=10.0)

    @pytest.mark.anyio
    async def test_consume_tokens_fractional(self):
        repo = AsyncMock()
        repo.consume.return_value = (9.5, NOW, 10.0)
        svc = _build_advice_service(repo=repo, daily_quota=10.0)

        result = await svc.consume_tokens(user_id=1, tokens=500)

        assert result.remaining == 9.5
        repo.consume.assert_awaited_once_with(user_id=1, amount=0.5, daily_total=10.0)


class TestPreCheckAndReserve:
    @pytest.mark.anyio
    async def test_pre_check_available(self):
        repo = AsyncMock()
        repo.get_quota.return_value = (5.0, NOW, 10.0)
        svc = _build_advice_service(repo=repo, daily_quota=10.0)

        result = await svc.pre_check_and_reserve(user_id=1, estimated_tokens=2000)

        assert result is True

    @pytest.mark.anyio
    async def test_pre_check_not_available(self):
        repo = AsyncMock()
        repo.get_quota.return_value = (0.5, NOW, 10.0)
        svc = _build_advice_service(repo=repo, daily_quota=10.0)

        result = await svc.pre_check_and_reserve(user_id=1, estimated_tokens=2000)

        assert result is False

    @pytest.mark.anyio
    async def test_pre_check_default_tokens(self):
        repo = AsyncMock()
        repo.get_quota.return_value = (1.0, NOW, 10.0)
        svc = _build_advice_service(repo=repo, daily_quota=10.0)

        result = await svc.pre_check_and_reserve(user_id=1)

        assert result is True
        # default estimated_tokens=1000 => 1.0 SEU, remaining=1.0 => True
