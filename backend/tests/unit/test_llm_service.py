"""Unit tests for AiAdviceService (services.py) and AIChatService (chat_service.py)."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.errors import ForbiddenError, NotFoundError
from app.domain.llm.chat_service import AVAILABLE_MODELS, AIChatService, ModelInfo
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


def _build_chat_service(conv_repo=None, msg_repo=None, quota_repo=None):
    conv_repo = conv_repo or AsyncMock()
    msg_repo = msg_repo or AsyncMock()
    quota_repo = quota_repo or AsyncMock()
    return AIChatService(
        conversation_repo=conv_repo,
        message_repo=msg_repo,
        quota_repo=quota_repo,
    )


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


# ===========================================================================
# AIChatService tests
# ===========================================================================


class TestListModels:
    def test_list_models_returns_all_available(self):
        svc = _build_chat_service()
        models = svc.list_models()

        assert len(models) == len(AVAILABLE_MODELS)
        for i, m in enumerate(AVAILABLE_MODELS):
            assert models[i]["id"] == m.id
            assert models[i]["name"] == m.name
            assert models[i]["provider"] == m.provider
            assert models[i]["seuCost"] == m.seu_cost
            assert models[i]["maxTokens"] == m.max_tokens
            assert models[i]["isAvailable"] == m.is_available


class TestCreateConversation:
    @pytest.mark.anyio
    async def test_create_conversation(self):
        conv_repo = AsyncMock()
        conv = _make_conversation()
        conv_repo.create.return_value = conv
        svc = _build_chat_service(conv_repo=conv_repo)

        result = await svc.create_conversation(user_id=42, title="Hello")

        assert result["id"] == 1
        assert result["userId"] == 42
        assert result["title"] == "Test conversation"
        conv_repo.create.assert_awaited_once_with(
            user_id=42,
            title="Hello",
            model_type="standard",
            module_type="GENERAL",
        )

    @pytest.mark.anyio
    async def test_create_conversation_no_title(self):
        conv_repo = AsyncMock()
        conv = _make_conversation(title=None)
        conv_repo.create.return_value = conv
        svc = _build_chat_service(conv_repo=conv_repo)

        result = await svc.create_conversation(user_id=42)

        assert result["title"] is None
        conv_repo.create.assert_awaited_once_with(
            user_id=42,
            title=None,
            model_type="standard",
            module_type="GENERAL",
        )


class TestListConversations:
    @pytest.mark.anyio
    async def test_list_conversations(self):
        conv_repo = AsyncMock()
        convs = [_make_conversation(id=1), _make_conversation(id=2)]
        page_info = {
            "pageStart": 1,
            "pageSize": 20,
            "hasMore": False,
            "nextStart": None,
        }
        conv_repo.list_by_user.return_value = (convs, page_info)
        svc = _build_chat_service(conv_repo=conv_repo)

        result, page = await svc.list_conversations(
            user_id=42, page_start=None, page_size=20
        )

        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["id"] == 2
        assert page == page_info
        conv_repo.list_by_user.assert_awaited_once_with(
            42, page_start=None, page_size=20
        )

    @pytest.mark.anyio
    async def test_list_conversations_with_pagination(self):
        conv_repo = AsyncMock()
        convs = [_make_conversation(id=5)]
        page_info = {"pageStart": 5, "pageSize": 10, "hasMore": True, "nextStart": 3}
        conv_repo.list_by_user.return_value = (convs, page_info)
        svc = _build_chat_service(conv_repo=conv_repo)

        result, page = await svc.list_conversations(
            user_id=42, page_start=5, page_size=10
        )

        assert len(result) == 1
        assert page["hasMore"] is True
        assert page["nextStart"] == 3


class TestGetConversation:
    @pytest.mark.anyio
    async def test_get_conversation_success(self):
        conv_repo = AsyncMock()
        msg_repo = AsyncMock()
        conv = _make_conversation(id=1, owner_id=42)
        conv_repo.get_by_id.return_value = conv
        msgs = [
            _make_message(id=10, role="user", content="Hi"),
            _make_message(id=11, role="assistant", content="Hello!"),
        ]
        msg_repo.list_by_conversation.return_value = msgs
        svc = _build_chat_service(conv_repo=conv_repo, msg_repo=msg_repo)

        result = await svc.get_conversation(conversation_id=1, user_id=42)

        assert result["id"] == 1
        assert len(result["messages"]) == 2
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][1]["role"] == "assistant"

    @pytest.mark.anyio
    async def test_get_conversation_not_found(self):
        conv_repo = AsyncMock()
        conv_repo.get_by_id.return_value = None
        svc = _build_chat_service(conv_repo=conv_repo)

        with pytest.raises(NotFoundError, match="Conversation not found"):
            await svc.get_conversation(conversation_id=999, user_id=42)

    @pytest.mark.anyio
    async def test_get_conversation_wrong_owner(self):
        conv_repo = AsyncMock()
        conv = _make_conversation(id=1, owner_id=42)
        conv_repo.get_by_id.return_value = conv
        svc = _build_chat_service(conv_repo=conv_repo)

        with pytest.raises(ForbiddenError, match="Access denied"):
            await svc.get_conversation(conversation_id=1, user_id=99)


class TestDeleteConversation:
    @pytest.mark.anyio
    async def test_delete_conversation_success(self):
        conv_repo = AsyncMock()
        conv = _make_conversation(id=1, owner_id=42)
        conv_repo.get_by_id.return_value = conv
        conv_repo.delete.return_value = True
        svc = _build_chat_service(conv_repo=conv_repo)

        result = await svc.delete_conversation(conversation_id=1, user_id=42)

        assert result is True
        conv_repo.delete.assert_awaited_once_with(1)

    @pytest.mark.anyio
    async def test_delete_conversation_not_found(self):
        conv_repo = AsyncMock()
        conv_repo.get_by_id.return_value = None
        svc = _build_chat_service(conv_repo=conv_repo)

        with pytest.raises(NotFoundError, match="Conversation not found"):
            await svc.delete_conversation(conversation_id=999, user_id=42)

    @pytest.mark.anyio
    async def test_delete_conversation_wrong_owner(self):
        conv_repo = AsyncMock()
        conv = _make_conversation(id=1, owner_id=42)
        conv_repo.get_by_id.return_value = conv
        svc = _build_chat_service(conv_repo=conv_repo)

        with pytest.raises(ForbiddenError, match="Access denied"):
            await svc.delete_conversation(conversation_id=1, user_id=99)


class TestUpdateConversationTitle:
    @pytest.mark.anyio
    async def test_update_title_success(self):
        conv_repo = AsyncMock()
        conv = _make_conversation(id=1, owner_id=42)
        conv_repo.get_by_id.return_value = conv
        updated = _make_conversation(id=1, owner_id=42, title="New Title")
        conv_repo.update_title.return_value = updated
        svc = _build_chat_service(conv_repo=conv_repo)

        result = await svc.update_conversation_title(
            conversation_id=1, user_id=42, title="New Title"
        )

        assert result["title"] == "New Title"
        conv_repo.update_title.assert_awaited_once_with(1, "New Title")

    @pytest.mark.anyio
    async def test_update_title_not_found(self):
        conv_repo = AsyncMock()
        conv_repo.get_by_id.return_value = None
        svc = _build_chat_service(conv_repo=conv_repo)

        with pytest.raises(NotFoundError, match="Conversation not found"):
            await svc.update_conversation_title(
                conversation_id=999, user_id=42, title="Title"
            )

    @pytest.mark.anyio
    async def test_update_title_wrong_owner(self):
        conv_repo = AsyncMock()
        conv = _make_conversation(id=1, owner_id=42)
        conv_repo.get_by_id.return_value = conv
        svc = _build_chat_service(conv_repo=conv_repo)

        with pytest.raises(ForbiddenError, match="Access denied"):
            await svc.update_conversation_title(
                conversation_id=1, user_id=99, title="Title"
            )

    @pytest.mark.anyio
    async def test_update_title_returns_none_after_ownership_check(self):
        """Cover the branch where update_title returns None (race condition)."""
        conv_repo = AsyncMock()
        conv = _make_conversation(id=1, owner_id=42)
        conv_repo.get_by_id.return_value = conv
        conv_repo.update_title.return_value = None
        svc = _build_chat_service(conv_repo=conv_repo)

        with pytest.raises(NotFoundError, match="Conversation not found"):
            await svc.update_conversation_title(
                conversation_id=1, user_id=42, title="Title"
            )


class TestChat:
    @pytest.mark.anyio
    async def test_chat_new_conversation(self):
        conv_repo = AsyncMock()
        msg_repo = AsyncMock()
        quota_repo = AsyncMock()

        # quota pre-check passes
        quota_repo.get_quota.return_value = (5.0, NOW, 10.0)

        # new conversation created
        new_conv = _make_conversation(id=10, owner_id=42)
        conv_repo.create.return_value = new_conv

        # user message saved
        user_msg = _make_message(id=100, role="user", content="Hi")
        history = [user_msg]
        msg_repo.create.side_effect = [
            user_msg,
            _make_message(id=101, role="assistant", content="Reply"),
        ]
        msg_repo.list_by_conversation.return_value = history

        # OpenAI response mock
        openai_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Reply"))],
            usage=SimpleNamespace(total_tokens=150),
        )

        # quota consume after response
        quota_repo.consume.return_value = (4.85, NOW, 10.0)

        svc = _build_chat_service(
            conv_repo=conv_repo, msg_repo=msg_repo, quota_repo=quota_repo
        )
        svc._client = AsyncMock()
        svc._client.chat.completions.create.return_value = openai_response

        result = await svc.chat(user_id=42, message="Hi")

        assert result["conversationId"] == 10
        assert result["message"]["content"] == "Reply"
        assert result["tokensUsed"] == 150
        assert result["seuConsumed"] == 0.15
        conv_repo.create.assert_awaited_once()

    @pytest.mark.anyio
    async def test_chat_existing_conversation(self):
        conv_repo = AsyncMock()
        msg_repo = AsyncMock()
        quota_repo = AsyncMock()

        quota_repo.get_quota.return_value = (5.0, NOW, 10.0)

        existing_conv = _make_conversation(id=5, owner_id=42)
        conv_repo.get_by_id.return_value = existing_conv

        user_msg = _make_message(id=200, role="user", content="Hello")
        assistant_msg = _make_message(id=201, role="assistant", content="World")
        msg_repo.create.side_effect = [user_msg, assistant_msg]
        msg_repo.list_by_conversation.return_value = [user_msg]

        openai_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="World"))],
            usage=SimpleNamespace(total_tokens=100),
        )

        quota_repo.consume.return_value = (4.9, NOW, 10.0)

        svc = _build_chat_service(
            conv_repo=conv_repo, msg_repo=msg_repo, quota_repo=quota_repo
        )
        svc._client = AsyncMock()
        svc._client.chat.completions.create.return_value = openai_response

        result = await svc.chat(user_id=42, message="Hello", conversation_id=5)

        assert result["conversationId"] == 5
        conv_repo.create.assert_not_awaited()
        conv_repo.get_by_id.assert_awaited_once_with(5)

    @pytest.mark.anyio
    async def test_chat_quota_exhausted(self):
        quota_repo = AsyncMock()
        quota_repo.get_quota.return_value = (0.0, NOW, 10.0)

        svc = _build_chat_service(quota_repo=quota_repo)

        with pytest.raises(QuotaExceededError, match="AI quota exhausted"):
            await svc.chat(user_id=42, message="Hi")

    @pytest.mark.anyio
    async def test_chat_existing_conversation_not_found(self):
        conv_repo = AsyncMock()
        quota_repo = AsyncMock()
        quota_repo.get_quota.return_value = (5.0, NOW, 10.0)
        conv_repo.get_by_id.return_value = None

        svc = _build_chat_service(conv_repo=conv_repo, quota_repo=quota_repo)

        with pytest.raises(NotFoundError, match="Conversation not found"):
            await svc.chat(user_id=42, message="Hi", conversation_id=999)

    @pytest.mark.anyio
    async def test_chat_existing_conversation_wrong_owner(self):
        conv_repo = AsyncMock()
        quota_repo = AsyncMock()
        quota_repo.get_quota.return_value = (5.0, NOW, 10.0)
        conv = _make_conversation(id=5, owner_id=42)
        conv_repo.get_by_id.return_value = conv

        svc = _build_chat_service(conv_repo=conv_repo, quota_repo=quota_repo)

        with pytest.raises(ForbiddenError, match="Access denied"):
            await svc.chat(user_id=99, message="Hi", conversation_id=5)

    @pytest.mark.anyio
    async def test_chat_long_message_title_truncated(self):
        """When message > 50 chars the title is message[:50]."""
        conv_repo = AsyncMock()
        msg_repo = AsyncMock()
        quota_repo = AsyncMock()

        quota_repo.get_quota.return_value = (5.0, NOW, 10.0)

        long_msg = "A" * 100
        new_conv = _make_conversation(id=10, owner_id=42, title=long_msg[:50])
        conv_repo.create.return_value = new_conv

        user_msg = _make_message(id=100, role="user", content=long_msg)
        assistant_msg = _make_message(id=101, role="assistant", content="OK")
        msg_repo.create.side_effect = [user_msg, assistant_msg]
        msg_repo.list_by_conversation.return_value = [user_msg]

        openai_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))],
            usage=SimpleNamespace(total_tokens=200),
        )

        quota_repo.consume.return_value = (4.8, NOW, 10.0)

        svc = _build_chat_service(
            conv_repo=conv_repo, msg_repo=msg_repo, quota_repo=quota_repo
        )
        svc._client = AsyncMock()
        svc._client.chat.completions.create.return_value = openai_response

        await svc.chat(user_id=42, message=long_msg)

        conv_repo.create.assert_awaited_once()
        call_kwargs = conv_repo.create.call_args.kwargs
        assert call_kwargs["title"] == "A" * 50

    @pytest.mark.anyio
    async def test_chat_short_message_uses_full_as_title(self):
        """When message <= 50 chars the full message is used as title."""
        conv_repo = AsyncMock()
        msg_repo = AsyncMock()
        quota_repo = AsyncMock()

        quota_repo.get_quota.return_value = (5.0, NOW, 10.0)

        short_msg = "Short message"
        new_conv = _make_conversation(id=10, owner_id=42, title=short_msg)
        conv_repo.create.return_value = new_conv

        user_msg = _make_message(id=100, role="user", content=short_msg)
        assistant_msg = _make_message(id=101, role="assistant", content="OK")
        msg_repo.create.side_effect = [user_msg, assistant_msg]
        msg_repo.list_by_conversation.return_value = [user_msg]

        openai_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))],
            usage=SimpleNamespace(total_tokens=50),
        )

        quota_repo.consume.return_value = (4.95, NOW, 10.0)

        svc = _build_chat_service(
            conv_repo=conv_repo, msg_repo=msg_repo, quota_repo=quota_repo
        )
        svc._client = AsyncMock()
        svc._client.chat.completions.create.return_value = openai_response

        await svc.chat(user_id=42, message=short_msg)

        call_kwargs = conv_repo.create.call_args.kwargs
        assert call_kwargs["title"] == short_msg

    @pytest.mark.anyio
    async def test_chat_no_usage_in_response(self):
        """When response.usage is None, tokens_used should be 0."""
        conv_repo = AsyncMock()
        msg_repo = AsyncMock()
        quota_repo = AsyncMock()

        quota_repo.get_quota.return_value = (5.0, NOW, 10.0)

        new_conv = _make_conversation(id=10, owner_id=42)
        conv_repo.create.return_value = new_conv

        user_msg = _make_message(id=100, role="user", content="Hi")
        assistant_msg = _make_message(id=101, role="assistant", content="Reply")
        msg_repo.create.side_effect = [user_msg, assistant_msg]
        msg_repo.list_by_conversation.return_value = [user_msg]

        openai_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Reply"))],
            usage=None,
        )

        quota_repo.consume.return_value = (5.0, NOW, 10.0)

        svc = _build_chat_service(
            conv_repo=conv_repo, msg_repo=msg_repo, quota_repo=quota_repo
        )
        svc._client = AsyncMock()
        svc._client.chat.completions.create.return_value = openai_response

        result = await svc.chat(user_id=42, message="Hi")

        assert result["tokensUsed"] == 0
        assert result["seuConsumed"] == 0.0

    @pytest.mark.anyio
    async def test_chat_empty_content_in_response(self):
        """When response.choices[0].message.content is None, we get empty string."""
        conv_repo = AsyncMock()
        msg_repo = AsyncMock()
        quota_repo = AsyncMock()

        quota_repo.get_quota.return_value = (5.0, NOW, 10.0)

        new_conv = _make_conversation(id=10, owner_id=42)
        conv_repo.create.return_value = new_conv

        user_msg = _make_message(id=100, role="user", content="Hi")
        assistant_msg = _make_message(id=101, role="assistant", content="")
        msg_repo.create.side_effect = [user_msg, assistant_msg]
        msg_repo.list_by_conversation.return_value = [user_msg]

        openai_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=None))],
            usage=SimpleNamespace(total_tokens=10),
        )

        quota_repo.consume.return_value = (4.99, NOW, 10.0)

        svc = _build_chat_service(
            conv_repo=conv_repo, msg_repo=msg_repo, quota_repo=quota_repo
        )
        svc._client = AsyncMock()
        svc._client.chat.completions.create.return_value = openai_response

        await svc.chat(user_id=42, message="Hi")

        # The assistant message content should be saved as ""
        second_create_call = msg_repo.create.call_args_list[1]
        assert second_create_call.kwargs["content"] == ""

    @pytest.mark.anyio
    async def test_chat_with_custom_model_id(self):
        conv_repo = AsyncMock()
        msg_repo = AsyncMock()
        quota_repo = AsyncMock()

        quota_repo.get_quota.return_value = (5.0, NOW, 10.0)

        new_conv = _make_conversation(id=10, owner_id=42)
        conv_repo.create.return_value = new_conv

        user_msg = _make_message(id=100, role="user")
        assistant_msg = _make_message(id=101, role="assistant")
        msg_repo.create.side_effect = [user_msg, assistant_msg]
        msg_repo.list_by_conversation.return_value = [user_msg]

        openai_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Reply"))],
            usage=SimpleNamespace(total_tokens=50),
        )

        quota_repo.consume.return_value = (4.95, NOW, 10.0)

        svc = _build_chat_service(
            conv_repo=conv_repo, msg_repo=msg_repo, quota_repo=quota_repo
        )
        svc._client = AsyncMock()
        svc._client.chat.completions.create.return_value = openai_response

        await svc.chat(user_id=42, message="Hi", model_id="gpt-4o")

        create_call = svc._client.chat.completions.create
        assert create_call.call_args.kwargs["model"] == "gpt-4o"


class TestCountTokens:
    def test_count_tokens_normal(self):
        svc = _build_chat_service()
        # tiktoken should handle normal text
        count = svc._count_tokens("Hello world")
        assert isinstance(count, int)
        assert count > 0

    def test_count_tokens_fallback_on_error(self):
        svc = _build_chat_service()
        with patch("app.domain.llm.chat_service.tiktoken") as mock_tiktoken:
            mock_tiktoken.encoding_for_model.side_effect = RuntimeError("fail")
            count = svc._count_tokens("Hello world test message")
            # fallback is len(text) // 4
            assert count == len("Hello world test message") // 4


class TestConversationToDict:
    def test_conversation_to_dict(self):
        svc = _build_chat_service()
        conv = _make_conversation(
            id=1,
            conversation_id="uuid-123",
            owner_id=42,
            title="My Chat",
            model_type="standard",
            module_type="GENERAL",
            created_at=NOW,
            updated_at=NOW,
        )

        result = svc._conversation_to_dict(conv)

        assert result["id"] == 1
        assert result["conversationId"] == "uuid-123"
        assert result["userId"] == 42
        assert result["title"] == "My Chat"
        assert result["modelType"] == "standard"
        assert result["moduleType"] == "GENERAL"
        assert isinstance(result["createdAt"], int)
        assert isinstance(result["updatedAt"], int)


class TestMessageToDict:
    def test_message_to_dict(self):
        svc = _build_chat_service()
        msg = _make_message(
            id=100,
            conversation_id=1,
            role="assistant",
            content="Hello!",
            tokens_used=50,
            created_at=NOW,
        )

        result = svc._message_to_dict(msg)

        assert result["id"] == 100
        assert result["conversationId"] == 1
        assert result["role"] == "assistant"
        assert result["content"] == "Hello!"
        assert result["tokensUsed"] == 50
        assert isinstance(result["createdAt"], int)


class TestModelInfoDataclass:
    def test_model_info_fields(self):
        m = ModelInfo(
            id="test-model",
            name="Test Model",
            provider="test",
            seu_cost=1.0,
            max_tokens=1024,
            is_available=True,
        )
        assert m.id == "test-model"
        assert m.name == "Test Model"
        assert m.provider == "test"
        assert m.seu_cost == 1.0
        assert m.max_tokens == 1024
        assert m.is_available is True


class TestQuotaInfoDataclass:
    def test_quota_info_fields(self):
        q = QuotaInfo(remaining=5.0, total=10.0, reset_time=NOW)
        assert q.remaining == 5.0
        assert q.total == 10.0
        assert q.reset_time == NOW


class TestQuotaExceededError:
    def test_quota_exceeded_error_message(self):
        err = QuotaExceededError("Out of quota")
        assert str(err) == "Out of quota"

    def test_quota_exceeded_error_is_exception(self):
        err = QuotaExceededError("fail")
        assert isinstance(err, Exception)
