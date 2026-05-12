"""Unit tests for TaskAIAdviceService."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.errors import QuotaExceededError
from app.domain.llm.llm_client import GeneratedAdvice, LLMResponse, StreamChunk
from app.domain.llm.services import QuotaInfo
from app.domain.task.task_ai_advice_service import TaskAIAdviceService
from app.domain.task.types import TaskAIAdviceStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _make_task(**overrides):
    defaults = {
        "id": 1,
        "name": "Test Task",
        "intro": "intro text",
        "description": "desc text",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_advice(**overrides):
    defaults = {
        "id": 10,
        "task_id": 1,
        "status": TaskAIAdviceStatus.COMPLETED.value,
        "topic_summary": json.dumps({"keyPoints": ["a"]}),
        "knowledge_fields": json.dumps([{"name": "f1", "description": "d1"}]),
        "learning_paths": json.dumps([{"stage": "s1", "description": "d1"}]),
        "methodology": json.dumps([{"step": "s1", "description": "d1"}]),
        "team_tips": json.dumps([{"role": "r1", "description": "d1"}]),
        "updated_at": _NOW,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_conversation(**overrides):
    defaults = {
        "id": 100,
        "conversation_id": "abc123",
        "context_id": 1,
        "owner_id": 42,
        "title": "Test convo",
        "created_at": _NOW,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_message(**overrides):
    defaults = {
        "id": 500,
        "role": "user",
        "content": "Hello",
        "created_at": _NOW,
        "tokens_used": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_quota(**overrides):
    defaults = {
        "remaining": 9.0,
        "total": 10.0,
        "reset_time": _NOW,
    }
    defaults.update(overrides)
    return QuotaInfo(**defaults)


def _build_service(
    advice_repo=None,
    conversation_repo=None,
    message_repo=None,
    context_repo=None,
    task_repo=None,
    quota_service=None,
    llm_client=None,
):
    return TaskAIAdviceService(
        advice_repo=advice_repo or AsyncMock(),
        conversation_repo=conversation_repo or AsyncMock(),
        message_repo=message_repo or AsyncMock(),
        context_repo=context_repo or AsyncMock(),
        task_repo=task_repo or AsyncMock(),
        quota_service=quota_service or AsyncMock(),
        llm_client=llm_client or AsyncMock(),
    )


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_default_llm_client_used_when_none(self):
        """When llm_client is not passed, a default LLMClient() is created."""
        with patch("app.domain.task.task_ai_advice_service.LLMClient") as mock_cls:
            instance = mock_cls.return_value
            svc = TaskAIAdviceService(
                advice_repo=AsyncMock(),
                conversation_repo=AsyncMock(),
                message_repo=AsyncMock(),
                context_repo=AsyncMock(),
                task_repo=AsyncMock(),
                quota_service=AsyncMock(),
            )
            mock_cls.assert_called_once()
            assert svc._llm_client is instance

    def test_explicit_llm_client_used(self):
        custom = MagicMock()
        svc = _build_service(llm_client=custom)
        assert svc._llm_client is custom


# ---------------------------------------------------------------------------
# request_advice
# ---------------------------------------------------------------------------


class TestRequestAdvice:
    @pytest.mark.anyio
    async def test_returns_cached_completed_advice(self):
        advice = _make_advice(status=TaskAIAdviceStatus.COMPLETED.value)
        advice_repo = AsyncMock()
        advice_repo.get_latest.return_value = advice

        quota_service = AsyncMock()
        quota_service.get_quota.return_value = _make_quota()

        svc = _build_service(advice_repo=advice_repo, quota_service=quota_service)
        status, quota = await svc.request_advice(task_id=1, user_id=42)

        assert status == TaskAIAdviceStatus.COMPLETED.value
        assert quota.remaining == 9.0
        # Should NOT have called check_quota since cached advice was returned
        quota_service.check_quota.assert_not_awaited()

    @pytest.mark.anyio
    async def test_generates_new_advice_when_none_exists(self):
        advice_repo = AsyncMock()
        advice_repo.get_latest.return_value = None

        new_advice = _make_advice()
        advice_repo.create.return_value = new_advice

        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = _make_task()

        generated = GeneratedAdvice(
            topic_summary={"keyPoints": ["a"]},
            knowledge_fields=[{"name": "f", "description": "d"}],
            learning_paths=[{"stage": "s", "description": "d"}],
            methodology=[{"step": "s", "description": "d"}],
            team_tips=[{"role": "r", "description": "d"}],
        )
        llm_client = AsyncMock()
        llm_client.generate_task_advice.return_value = generated

        quota_service = AsyncMock()
        quota_service.check_quota.return_value = True
        quota_service.get_quota.return_value = _make_quota()

        context_repo = AsyncMock()

        svc = _build_service(
            advice_repo=advice_repo,
            task_repo=task_repo,
            quota_service=quota_service,
            llm_client=llm_client,
            context_repo=context_repo,
        )
        status, quota = await svc.request_advice(task_id=1, user_id=42)

        assert status == TaskAIAdviceStatus.COMPLETED.value
        quota_service.check_quota.assert_awaited_once_with(user_id=42, amount=1.0)
        quota_service.consume_quota.assert_awaited_once_with(user_id=42, amount=1.0)

    @pytest.mark.anyio
    async def test_generates_new_advice_when_not_completed(self):
        """If latest advice exists but is not COMPLETED, generate fresh advice."""
        advice_repo = AsyncMock()
        advice_repo.get_latest.return_value = _make_advice(status=TaskAIAdviceStatus.FAILED.value)

        new_advice = _make_advice()
        advice_repo.create.return_value = new_advice

        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = _make_task()

        generated = GeneratedAdvice(
            topic_summary={"keyPoints": []},
            knowledge_fields=[],
            learning_paths=[],
            methodology=[],
            team_tips=[],
        )
        llm_client = AsyncMock()
        llm_client.generate_task_advice.return_value = generated

        quota_service = AsyncMock()
        quota_service.check_quota.return_value = True
        quota_service.get_quota.return_value = _make_quota()

        svc = _build_service(
            advice_repo=advice_repo,
            task_repo=task_repo,
            quota_service=quota_service,
            llm_client=llm_client,
        )
        status, _ = await svc.request_advice(task_id=1, user_id=42)

        assert status == TaskAIAdviceStatus.COMPLETED.value
        quota_service.check_quota.assert_awaited_once()

    @pytest.mark.anyio
    async def test_raises_quota_exceeded_when_no_quota(self):
        advice_repo = AsyncMock()
        advice_repo.get_latest.return_value = None

        quota_service = AsyncMock()
        quota_service.check_quota.return_value = False

        svc = _build_service(advice_repo=advice_repo, quota_service=quota_service)

        with pytest.raises(QuotaExceededError, match="AI quota exhausted"):
            await svc.request_advice(task_id=1, user_id=42)


# ---------------------------------------------------------------------------
# list_advices
# ---------------------------------------------------------------------------


class TestListAdvices:
    @pytest.mark.anyio
    async def test_returns_serialized_list(self):
        advices = [_make_advice(id=1), _make_advice(id=2)]
        advice_repo = AsyncMock()
        advice_repo.list_by_task.return_value = advices

        svc = _build_service(advice_repo=advice_repo)
        result = await svc.list_advices(task_id=1)

        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["id"] == 2
        assert result[0]["status"] == TaskAIAdviceStatus.COMPLETED.value

    @pytest.mark.anyio
    async def test_empty_list(self):
        advice_repo = AsyncMock()
        advice_repo.list_by_task.return_value = []

        svc = _build_service(advice_repo=advice_repo)
        result = await svc.list_advices(task_id=1)

        assert result == []


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


class TestGetStatus:
    @pytest.mark.anyio
    async def test_returns_none_when_no_advice(self):
        advice_repo = AsyncMock()
        advice_repo.get_latest.return_value = None

        svc = _build_service(advice_repo=advice_repo)
        result = await svc.get_status(task_id=1)

        assert result == "NONE"

    @pytest.mark.anyio
    async def test_returns_status_of_latest_advice(self):
        advice_repo = AsyncMock()
        advice_repo.get_latest.return_value = _make_advice(
            status=TaskAIAdviceStatus.PROCESSING.value
        )

        svc = _build_service(advice_repo=advice_repo)
        result = await svc.get_status(task_id=1)

        assert result == "PROCESSING"


# ---------------------------------------------------------------------------
# create_conversation
# ---------------------------------------------------------------------------


class TestCreateConversation:
    @pytest.mark.anyio
    async def test_creates_new_conversation_when_no_id(self):
        quota_service = AsyncMock()
        quota_service.check_quota.return_value = True
        quota_service.consume_tokens.return_value = _make_quota()

        convo = _make_conversation()
        conversation_repo = AsyncMock()
        conversation_repo.create.return_value = convo
        conversation_repo.get_by_conversation_id.return_value = convo

        user_msg = _make_message(id=501, role="user")
        message_repo = AsyncMock()
        message_repo.create_message.return_value = user_msg
        message_repo.list_for_conversation.return_value = [user_msg]

        llm_response = LLMResponse(
            content="AI answer",
            total_tokens=200,
            prompt_tokens=100,
            completion_tokens=100,
        )
        llm_client = AsyncMock()
        llm_client.get_completion_with_history.return_value = llm_response

        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = _make_task()

        svc = _build_service(
            conversation_repo=conversation_repo,
            message_repo=message_repo,
            quota_service=quota_service,
            llm_client=llm_client,
            task_repo=task_repo,
        )
        payload, quota = await svc.create_conversation(
            task_id=1,
            user_id=42,
            question="How to start?",
            parent_id=None,
            context=None,
        )

        assert "conversation" in payload
        conversation_repo.create.assert_awaited_once()
        quota_service.consume_tokens.assert_awaited_once_with(user_id=42, tokens=200)

    @pytest.mark.anyio
    async def test_uses_existing_conversation_when_id_given(self):
        quota_service = AsyncMock()
        quota_service.check_quota.return_value = True
        quota_service.consume_tokens.return_value = _make_quota()

        convo = _make_conversation(conversation_id="existing123", context_id=1, owner_id=42)
        conversation_repo = AsyncMock()
        conversation_repo.get_by_conversation_id.return_value = convo

        user_msg = _make_message(id=501, role="user")
        message_repo = AsyncMock()
        message_repo.create_message.return_value = user_msg
        message_repo.list_for_conversation.return_value = [user_msg]

        llm_response = LLMResponse(
            content="answer",
            total_tokens=100,
            prompt_tokens=50,
            completion_tokens=50,
        )
        llm_client = AsyncMock()
        llm_client.get_completion_with_history.return_value = llm_response

        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = _make_task()

        svc = _build_service(
            conversation_repo=conversation_repo,
            message_repo=message_repo,
            quota_service=quota_service,
            llm_client=llm_client,
            task_repo=task_repo,
        )
        payload, _ = await svc.create_conversation(
            task_id=1,
            user_id=42,
            question="Follow up",
            parent_id=501,
            context=None,
            conversation_id="existing123",
        )

        # Should not have called create since we reused existing conversation
        conversation_repo.create.assert_not_awaited()

    @pytest.mark.anyio
    async def test_raises_when_conversation_not_found(self):
        quota_service = AsyncMock()
        quota_service.check_quota.return_value = True

        conversation_repo = AsyncMock()
        conversation_repo.get_by_conversation_id.return_value = None

        svc = _build_service(
            conversation_repo=conversation_repo,
            quota_service=quota_service,
        )

        with pytest.raises(ValueError, match="Conversation not found"):
            await svc.create_conversation(
                task_id=1,
                user_id=42,
                question="Q",
                parent_id=None,
                context=None,
                conversation_id="nonexistent",
            )

    @pytest.mark.anyio
    async def test_raises_when_conversation_wrong_task(self):
        """Conversation found but belongs to a different task_id."""
        quota_service = AsyncMock()
        quota_service.check_quota.return_value = True

        convo = _make_conversation(context_id=999, owner_id=42)
        conversation_repo = AsyncMock()
        conversation_repo.get_by_conversation_id.return_value = convo

        svc = _build_service(
            conversation_repo=conversation_repo,
            quota_service=quota_service,
        )

        with pytest.raises(ValueError, match="Conversation not found"):
            await svc.create_conversation(
                task_id=1,
                user_id=42,
                question="Q",
                parent_id=None,
                context=None,
                conversation_id="abc123",
            )

    @pytest.mark.anyio
    async def test_raises_when_conversation_wrong_owner(self):
        """Conversation found but belongs to a different user_id."""
        quota_service = AsyncMock()
        quota_service.check_quota.return_value = True

        convo = _make_conversation(context_id=1, owner_id=999)
        conversation_repo = AsyncMock()
        conversation_repo.get_by_conversation_id.return_value = convo

        svc = _build_service(
            conversation_repo=conversation_repo,
            quota_service=quota_service,
        )

        with pytest.raises(ValueError, match="Conversation not found"):
            await svc.create_conversation(
                task_id=1,
                user_id=42,
                question="Q",
                parent_id=None,
                context=None,
                conversation_id="abc123",
            )

    @pytest.mark.anyio
    async def test_raises_quota_exceeded(self):
        quota_service = AsyncMock()
        quota_service.check_quota.return_value = False

        svc = _build_service(quota_service=quota_service)

        with pytest.raises(QuotaExceededError, match="AI quota exhausted"):
            await svc.create_conversation(
                task_id=1,
                user_id=42,
                question="Q",
                parent_id=None,
                context=None,
            )

    @pytest.mark.anyio
    async def test_context_with_section_creates_context_record(self):
        quota_service = AsyncMock()
        quota_service.check_quota.return_value = True
        quota_service.consume_tokens.return_value = _make_quota()

        convo = _make_conversation()
        conversation_repo = AsyncMock()
        conversation_repo.create.return_value = convo
        conversation_repo.get_by_conversation_id.return_value = convo

        user_msg = _make_message(id=501)
        message_repo = AsyncMock()
        message_repo.create_message.return_value = user_msg
        message_repo.list_for_conversation.return_value = [user_msg]

        llm_response = LLMResponse(
            content="a", total_tokens=10, prompt_tokens=5, completion_tokens=5
        )
        llm_client = AsyncMock()
        llm_client.get_completion_with_history.return_value = llm_response

        context_repo = AsyncMock()
        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = _make_task()

        svc = _build_service(
            conversation_repo=conversation_repo,
            message_repo=message_repo,
            quota_service=quota_service,
            llm_client=llm_client,
            context_repo=context_repo,
            task_repo=task_repo,
        )
        await svc.create_conversation(
            task_id=1,
            user_id=42,
            question="Q",
            parent_id=None,
            context={"section": "methodology", "sectionIndex": 2},
        )

        context_repo.get_or_create.assert_awaited_once_with(
            task_id=1,
            section="methodology",
            section_index=2,
        )

    @pytest.mark.anyio
    async def test_context_with_index_key_fallback(self):
        """When context has 'index' instead of 'sectionIndex'."""
        quota_service = AsyncMock()
        quota_service.check_quota.return_value = True
        quota_service.consume_tokens.return_value = _make_quota()

        convo = _make_conversation()
        conversation_repo = AsyncMock()
        conversation_repo.create.return_value = convo
        conversation_repo.get_by_conversation_id.return_value = convo

        user_msg = _make_message(id=501)
        message_repo = AsyncMock()
        message_repo.create_message.return_value = user_msg
        message_repo.list_for_conversation.return_value = [user_msg]

        llm_response = LLMResponse(
            content="a", total_tokens=10, prompt_tokens=5, completion_tokens=5
        )
        llm_client = AsyncMock()
        llm_client.get_completion_with_history.return_value = llm_response

        context_repo = AsyncMock()
        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = _make_task()

        svc = _build_service(
            conversation_repo=conversation_repo,
            message_repo=message_repo,
            quota_service=quota_service,
            llm_client=llm_client,
            context_repo=context_repo,
            task_repo=task_repo,
        )
        await svc.create_conversation(
            task_id=1,
            user_id=42,
            question="Q",
            parent_id=None,
            context={"section": "knowledge_fields", "index": 0},
        )

        context_repo.get_or_create.assert_awaited_once_with(
            task_id=1,
            section="knowledge_fields",
            section_index=0,
        )

    @pytest.mark.anyio
    async def test_context_without_section_skips_context_creation(self):
        """When context dict has no 'section' key, no context record is created."""
        quota_service = AsyncMock()
        quota_service.check_quota.return_value = True
        quota_service.consume_tokens.return_value = _make_quota()

        convo = _make_conversation()
        conversation_repo = AsyncMock()
        conversation_repo.create.return_value = convo
        conversation_repo.get_by_conversation_id.return_value = convo

        user_msg = _make_message(id=501)
        message_repo = AsyncMock()
        message_repo.create_message.return_value = user_msg
        message_repo.list_for_conversation.return_value = [user_msg]

        llm_response = LLMResponse(
            content="a", total_tokens=10, prompt_tokens=5, completion_tokens=5
        )
        llm_client = AsyncMock()
        llm_client.get_completion_with_history.return_value = llm_response

        context_repo = AsyncMock()
        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = _make_task()

        svc = _build_service(
            conversation_repo=conversation_repo,
            message_repo=message_repo,
            quota_service=quota_service,
            llm_client=llm_client,
            context_repo=context_repo,
            task_repo=task_repo,
        )
        await svc.create_conversation(
            task_id=1,
            user_id=42,
            question="Q",
            parent_id=None,
            context={"unrelated": "data"},
        )

        context_repo.get_or_create.assert_not_awaited()

    @pytest.mark.anyio
    async def test_none_context_skips_context_creation(self):
        """When context is None, no context record is created."""
        quota_service = AsyncMock()
        quota_service.check_quota.return_value = True
        quota_service.consume_tokens.return_value = _make_quota()

        convo = _make_conversation()
        conversation_repo = AsyncMock()
        conversation_repo.create.return_value = convo
        conversation_repo.get_by_conversation_id.return_value = convo

        user_msg = _make_message(id=501)
        message_repo = AsyncMock()
        message_repo.create_message.return_value = user_msg
        message_repo.list_for_conversation.return_value = [user_msg]

        llm_response = LLMResponse(
            content="a", total_tokens=10, prompt_tokens=5, completion_tokens=5
        )
        llm_client = AsyncMock()
        llm_client.get_completion_with_history.return_value = llm_response

        context_repo = AsyncMock()
        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = _make_task()

        svc = _build_service(
            conversation_repo=conversation_repo,
            message_repo=message_repo,
            quota_service=quota_service,
            llm_client=llm_client,
            context_repo=context_repo,
            task_repo=task_repo,
        )
        await svc.create_conversation(
            task_id=1,
            user_id=42,
            question="Q",
            parent_id=None,
            context=None,
        )

        context_repo.get_or_create.assert_not_awaited()


# ---------------------------------------------------------------------------
# stream_conversation
# ---------------------------------------------------------------------------


class TestStreamConversation:
    @pytest.mark.anyio
    async def test_streams_new_conversation_with_tokens(self):
        quota_service = AsyncMock()
        quota_service.check_quota.return_value = True
        quota_service.consume_tokens.return_value = _make_quota()

        convo = _make_conversation()
        conversation_repo = AsyncMock()
        conversation_repo.create.return_value = convo

        user_msg = _make_message(id=501, role="user")
        message_repo = AsyncMock()
        message_repo.create_message.return_value = user_msg
        message_repo.list_for_conversation.return_value = []

        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = _make_task()

        chunks = [
            StreamChunk(content="Hello", is_final=False, total_tokens=None),
            StreamChunk(content=" world", is_final=False, total_tokens=None),
            StreamChunk(content="", is_final=True, total_tokens=150),
        ]

        async def fake_stream(**kwargs):
            for c in chunks:
                yield c

        llm_client = AsyncMock()
        llm_client.stream_completion_with_history = fake_stream

        svc = _build_service(
            conversation_repo=conversation_repo,
            message_repo=message_repo,
            quota_service=quota_service,
            llm_client=llm_client,
            task_repo=task_repo,
        )

        collected = []
        async for chunk in svc.stream_conversation(
            task_id=1,
            user_id=42,
            question="Hello?",
        ):
            collected.append(chunk)

        assert len(collected) == 3
        assert collected[0].content == "Hello"
        assert collected[1].content == " world"
        assert collected[2].total_tokens == 150

        # Assistant message should be saved with accumulated content
        calls = message_repo.create_message.call_args_list
        # Last call should be the assistant message
        assistant_call = calls[-1]
        assert assistant_call.kwargs["role"] == "assistant"
        assert assistant_call.kwargs["content"] == "Hello world"
        assert assistant_call.kwargs["tokens_used"] == 150

        quota_service.consume_tokens.assert_awaited_once_with(user_id=42, tokens=150)

    @pytest.mark.anyio
    async def test_streams_existing_conversation(self):
        quota_service = AsyncMock()
        quota_service.check_quota.return_value = True

        convo = _make_conversation(context_id=1, owner_id=42)
        conversation_repo = AsyncMock()
        conversation_repo.get_by_conversation_id.return_value = convo

        user_msg = _make_message(id=501)
        message_repo = AsyncMock()
        message_repo.create_message.return_value = user_msg
        message_repo.list_for_conversation.return_value = []

        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = _make_task()

        async def fake_stream(**kwargs):
            yield StreamChunk(content="answer", is_final=True, total_tokens=50)

        llm_client = AsyncMock()
        llm_client.stream_completion_with_history = fake_stream

        svc = _build_service(
            conversation_repo=conversation_repo,
            message_repo=message_repo,
            quota_service=quota_service,
            llm_client=llm_client,
            task_repo=task_repo,
        )

        collected = []
        async for chunk in svc.stream_conversation(
            task_id=1,
            user_id=42,
            question="Follow up",
            conversation_id="abc123",
        ):
            collected.append(chunk)

        assert len(collected) == 1
        conversation_repo.create.assert_not_awaited()

    @pytest.mark.anyio
    async def test_stream_raises_quota_exceeded(self):
        quota_service = AsyncMock()
        quota_service.check_quota.return_value = False

        svc = _build_service(quota_service=quota_service)

        with pytest.raises(QuotaExceededError, match="AI quota exhausted"):
            async for _ in svc.stream_conversation(
                task_id=1,
                user_id=42,
                question="Q",
            ):
                pass  # pragma: no cover

    @pytest.mark.anyio
    async def test_stream_raises_when_conversation_not_found(self):
        quota_service = AsyncMock()
        quota_service.check_quota.return_value = True

        conversation_repo = AsyncMock()
        conversation_repo.get_by_conversation_id.return_value = None

        svc = _build_service(
            conversation_repo=conversation_repo,
            quota_service=quota_service,
        )

        with pytest.raises(ValueError, match="Conversation not found"):
            async for _ in svc.stream_conversation(
                task_id=1,
                user_id=42,
                question="Q",
                conversation_id="nonexistent",
            ):
                pass  # pragma: no cover

    @pytest.mark.anyio
    async def test_stream_raises_when_conversation_wrong_task(self):
        quota_service = AsyncMock()
        quota_service.check_quota.return_value = True

        convo = _make_conversation(context_id=999, owner_id=42)
        conversation_repo = AsyncMock()
        conversation_repo.get_by_conversation_id.return_value = convo

        svc = _build_service(
            conversation_repo=conversation_repo,
            quota_service=quota_service,
        )

        with pytest.raises(ValueError, match="Conversation not found"):
            async for _ in svc.stream_conversation(
                task_id=1,
                user_id=42,
                question="Q",
                conversation_id="abc123",
            ):
                pass  # pragma: no cover

    @pytest.mark.anyio
    async def test_stream_raises_when_conversation_wrong_owner(self):
        quota_service = AsyncMock()
        quota_service.check_quota.return_value = True

        convo = _make_conversation(context_id=1, owner_id=999)
        conversation_repo = AsyncMock()
        conversation_repo.get_by_conversation_id.return_value = convo

        svc = _build_service(
            conversation_repo=conversation_repo,
            quota_service=quota_service,
        )

        with pytest.raises(ValueError, match="Conversation not found"):
            async for _ in svc.stream_conversation(
                task_id=1,
                user_id=42,
                question="Q",
                conversation_id="abc123",
            ):
                pass  # pragma: no cover

    @pytest.mark.anyio
    async def test_stream_skips_consume_tokens_when_zero(self):
        """When total_tokens is 0, consume_tokens should not be called."""
        quota_service = AsyncMock()
        quota_service.check_quota.return_value = True

        convo = _make_conversation()
        conversation_repo = AsyncMock()
        conversation_repo.create.return_value = convo

        user_msg = _make_message(id=501)
        message_repo = AsyncMock()
        message_repo.create_message.return_value = user_msg
        message_repo.list_for_conversation.return_value = []

        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = _make_task()

        async def fake_stream(**kwargs):
            yield StreamChunk(content="text", is_final=True, total_tokens=None)

        llm_client = AsyncMock()
        llm_client.stream_completion_with_history = fake_stream

        svc = _build_service(
            conversation_repo=conversation_repo,
            message_repo=message_repo,
            quota_service=quota_service,
            llm_client=llm_client,
            task_repo=task_repo,
        )

        async for _ in svc.stream_conversation(task_id=1, user_id=42, question="Q"):
            pass

        quota_service.consume_tokens.assert_not_awaited()

    @pytest.mark.anyio
    async def test_stream_with_context(self):
        """Verify that context is passed to _build_message_history in stream mode."""
        quota_service = AsyncMock()
        quota_service.check_quota.return_value = True
        quota_service.consume_tokens.return_value = _make_quota()

        convo = _make_conversation()
        conversation_repo = AsyncMock()
        conversation_repo.create.return_value = convo

        user_msg = _make_message(id=501)
        message_repo = AsyncMock()
        message_repo.create_message.return_value = user_msg
        message_repo.list_for_conversation.return_value = []

        task = _make_task()
        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = task

        captured_messages = []

        async def fake_stream(*, messages):
            captured_messages.extend(messages)
            yield StreamChunk(content="ok", is_final=True, total_tokens=10)

        llm_client = AsyncMock()
        llm_client.stream_completion_with_history = fake_stream

        svc = _build_service(
            conversation_repo=conversation_repo,
            message_repo=message_repo,
            quota_service=quota_service,
            llm_client=llm_client,
            task_repo=task_repo,
        )

        async for _ in svc.stream_conversation(
            task_id=1,
            user_id=42,
            question="Q",
            context={"section": "methodology", "sectionIndex": 1},
        ):
            pass

        # The system prompt should include context info
        system_msg = captured_messages[0]
        assert system_msg["role"] == "system"
        assert "methodology" in system_msg["content"]


# ---------------------------------------------------------------------------
# _build_message_history
# ---------------------------------------------------------------------------


class TestBuildMessageHistory:
    @pytest.mark.anyio
    async def test_builds_system_prompt_with_task_context(self):
        task = _make_task(name="My Task", intro="My Intro", description="My Desc")
        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = task

        message_repo = AsyncMock()
        message_repo.list_for_conversation.return_value = []

        svc = _build_service(task_repo=task_repo, message_repo=message_repo)
        history = await svc._build_message_history(100, 1)

        assert len(history) == 1
        assert history[0]["role"] == "system"
        assert "My Task" in history[0]["content"]
        assert "My Intro" in history[0]["content"]
        assert "My Desc" in history[0]["content"]

    @pytest.mark.anyio
    async def test_builds_with_no_task(self):
        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = None

        message_repo = AsyncMock()
        message_repo.list_for_conversation.return_value = []

        svc = _build_service(task_repo=task_repo, message_repo=message_repo)
        history = await svc._build_message_history(100, 1)

        assert len(history) == 1
        assert history[0]["role"] == "system"

    @pytest.mark.anyio
    async def test_builds_with_task_missing_fields(self):
        """Task with None intro/description."""
        task = _make_task(name="Task", intro=None, description=None)
        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = task

        message_repo = AsyncMock()
        message_repo.list_for_conversation.return_value = []

        svc = _build_service(task_repo=task_repo, message_repo=message_repo)
        history = await svc._build_message_history(100, 1)

        # Should still have system message without crashing
        assert history[0]["role"] == "system"

    @pytest.mark.anyio
    async def test_builds_with_context_section_and_index(self):
        task = _make_task()
        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = task

        message_repo = AsyncMock()
        message_repo.list_for_conversation.return_value = []

        svc = _build_service(task_repo=task_repo, message_repo=message_repo)
        history = await svc._build_message_history(
            100, 1, context={"section": "learning_paths", "sectionIndex": 3}
        )

        assert "learning_paths" in history[0]["content"]
        assert "3" in history[0]["content"]

    @pytest.mark.anyio
    async def test_builds_with_context_section_no_index(self):
        task = _make_task()
        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = task

        message_repo = AsyncMock()
        message_repo.list_for_conversation.return_value = []

        svc = _build_service(task_repo=task_repo, message_repo=message_repo)
        history = await svc._build_message_history(100, 1, context={"section": "knowledge_fields"})

        assert "knowledge_fields" in history[0]["content"]

    @pytest.mark.anyio
    async def test_builds_with_context_index_fallback(self):
        """Uses 'index' key when 'sectionIndex' is absent."""
        task = _make_task()
        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = task

        message_repo = AsyncMock()
        message_repo.list_for_conversation.return_value = []

        svc = _build_service(task_repo=task_repo, message_repo=message_repo)
        history = await svc._build_message_history(
            100, 1, context={"section": "team_tips", "index": 5}
        )

        assert "team_tips" in history[0]["content"]
        assert "5" in history[0]["content"]

    @pytest.mark.anyio
    async def test_builds_with_empty_section_string(self):
        """Empty string section should not include context_info."""
        task = _make_task()
        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = task

        message_repo = AsyncMock()
        message_repo.list_for_conversation.return_value = []

        svc = _build_service(task_repo=task_repo, message_repo=message_repo)
        history = await svc._build_message_history(100, 1, context={"section": ""})

        # Empty section should not add context_info block
        assert "当前关注的内容区块" not in history[0]["content"]

    @pytest.mark.anyio
    async def test_includes_existing_messages(self):
        task = _make_task()
        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = task

        messages = [
            _make_message(role="user", content="Q1"),
            _make_message(role="assistant", content="A1"),
        ]
        message_repo = AsyncMock()
        message_repo.list_for_conversation.return_value = messages

        svc = _build_service(task_repo=task_repo, message_repo=message_repo)
        history = await svc._build_message_history(100, 1)

        assert len(history) == 3  # system + 2 messages
        assert history[1] == {"role": "user", "content": "Q1"}
        assert history[2] == {"role": "assistant", "content": "A1"}


# ---------------------------------------------------------------------------
# list_conversations_grouped
# ---------------------------------------------------------------------------


class TestListConversationsGrouped:
    @pytest.mark.anyio
    async def test_groups_conversations(self):
        convos = [
            _make_conversation(conversation_id="c1", title="Title 1", created_at=_NOW),
            _make_conversation(conversation_id="c2", title=None, created_at=_NOW),
        ]
        conversation_repo = AsyncMock()
        conversation_repo.list_for_task.return_value = convos

        svc = _build_service(conversation_repo=conversation_repo)
        result = await svc.list_conversations_grouped(task_id=1)

        assert len(result) == 2
        assert result[0]["conversationId"] == "c1"
        assert result[0]["title"] == "Title 1"
        # None title falls back to default
        assert result[1]["title"] == "AI 对话"

    @pytest.mark.anyio
    async def test_empty_conversations(self):
        conversation_repo = AsyncMock()
        conversation_repo.list_for_task.return_value = []

        svc = _build_service(conversation_repo=conversation_repo)
        result = await svc.list_conversations_grouped(task_id=1)

        assert result == []


# ---------------------------------------------------------------------------
# get_conversation
# ---------------------------------------------------------------------------


class TestGetConversation:
    @pytest.mark.anyio
    async def test_returns_conversation_payload(self):
        convo = _make_conversation(conversation_id="c1", title="T1", created_at=_NOW)
        msgs = [
            _make_message(id=1, role="user", content="Q", tokens_used=None),
            _make_message(id=2, role="assistant", content="A", tokens_used=50),
        ]

        conversation_repo = AsyncMock()
        conversation_repo.get_by_conversation_id.return_value = convo

        message_repo = AsyncMock()
        message_repo.list_for_conversation.return_value = msgs

        svc = _build_service(
            conversation_repo=conversation_repo,
            message_repo=message_repo,
        )
        result = await svc.get_conversation(conversation_id="c1")

        assert result["conversation"]["conversationId"] == "c1"
        assert result["conversation"]["title"] == "T1"
        assert len(result["conversation"]["messages"]) == 2
        assert result["conversation"]["messages"][0]["role"] == "user"
        assert result["conversation"]["messages"][1]["tokensUsed"] == 50

    @pytest.mark.anyio
    async def test_raises_when_not_found(self):
        conversation_repo = AsyncMock()
        conversation_repo.get_by_conversation_id.return_value = None

        svc = _build_service(conversation_repo=conversation_repo)

        with pytest.raises(ValueError, match="Conversation not found"):
            await svc.get_conversation(conversation_id="nonexistent")


# ---------------------------------------------------------------------------
# delete_conversation
# ---------------------------------------------------------------------------


class TestDeleteConversation:
    @pytest.mark.anyio
    async def test_deletes_existing_conversation(self):
        convo = _make_conversation()
        conversation_repo = AsyncMock()
        conversation_repo.get_by_conversation_id.return_value = convo

        svc = _build_service(conversation_repo=conversation_repo)
        await svc.delete_conversation(conversation_id="abc123")

        conversation_repo.soft_delete.assert_awaited_once_with(convo)

    @pytest.mark.anyio
    async def test_noop_when_not_found(self):
        conversation_repo = AsyncMock()
        conversation_repo.get_by_conversation_id.return_value = None

        svc = _build_service(conversation_repo=conversation_repo)
        await svc.delete_conversation(conversation_id="nonexistent")

        conversation_repo.soft_delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# _generate_advice (private)
# ---------------------------------------------------------------------------


class TestGenerateAdvice:
    @pytest.mark.anyio
    async def test_generates_and_stores_advice(self):
        task = _make_task()
        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = task

        generated = GeneratedAdvice(
            topic_summary={"keyPoints": ["p1"]},
            knowledge_fields=[{"name": "kf1", "description": "d"}],
            learning_paths=[{"stage": "s1", "description": "d"}],
            methodology=[{"step": "m1", "description": "d"}],
            team_tips=[{"role": "r1", "description": "d"}],
        )
        llm_client = AsyncMock()
        llm_client.generate_task_advice.return_value = generated

        advice = _make_advice()
        advice_repo = AsyncMock()
        advice_repo.create.return_value = advice

        context_repo = AsyncMock()

        svc = _build_service(
            task_repo=task_repo,
            llm_client=llm_client,
            advice_repo=advice_repo,
            context_repo=context_repo,
        )
        result = await svc._generate_advice(1)

        assert result is advice
        advice_repo.create.assert_awaited_once()
        create_kwargs = advice_repo.create.call_args.kwargs
        assert create_kwargs["task_id"] == 1
        assert create_kwargs["status"] == TaskAIAdviceStatus.COMPLETED.value
        # topic_summary should be JSON
        assert json.loads(create_kwargs["topic_summary"]) == {"keyPoints": ["p1"]}

    @pytest.mark.anyio
    async def test_raises_when_task_not_found(self):
        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = None

        svc = _build_service(task_repo=task_repo)

        with pytest.raises(ValueError, match="Task not found"):
            await svc._generate_advice(999)


# ---------------------------------------------------------------------------
# _ensure_contexts (private)
# ---------------------------------------------------------------------------


class TestEnsureContexts:
    @pytest.mark.anyio
    async def test_creates_contexts_for_all_sections(self):
        context_repo = AsyncMock()
        svc = _build_service(context_repo=context_repo)

        payload = GeneratedAdvice(
            topic_summary={"keyPoints": ["a"]},
            knowledge_fields=[{"name": "kf1"}, {"name": "kf2"}],
            learning_paths=[{"stage": "s1"}],
            methodology=[],
            team_tips=[{"role": "r1"}, {"role": "r2"}, {"role": "r3"}],
        )

        await svc._ensure_contexts(1, payload)

        # 2 knowledge_fields + 1 learning_path + 0 methodology + 3 team_tips = 6
        assert context_repo.get_or_create.await_count == 6

    @pytest.mark.anyio
    async def test_handles_none_items(self):
        """When a section is None, it should be treated as empty list."""
        context_repo = AsyncMock()
        svc = _build_service(context_repo=context_repo)

        payload = GeneratedAdvice(
            topic_summary={"keyPoints": []},
            knowledge_fields=None,
            learning_paths=None,
            methodology=None,
            team_tips=None,
        )

        await svc._ensure_contexts(1, payload)

        context_repo.get_or_create.assert_not_awaited()

    @pytest.mark.anyio
    async def test_creates_correct_indices(self):
        context_repo = AsyncMock()
        svc = _build_service(context_repo=context_repo)

        payload = GeneratedAdvice(
            topic_summary={"keyPoints": []},
            knowledge_fields=[{"name": "a"}, {"name": "b"}],
            learning_paths=[],
            methodology=[],
            team_tips=[],
        )

        await svc._ensure_contexts(1, payload)

        calls = context_repo.get_or_create.call_args_list
        assert calls[0].kwargs == {"task_id": 1, "section": "knowledge_fields", "section_index": 0}
        assert calls[1].kwargs == {"task_id": 1, "section": "knowledge_fields", "section_index": 1}


# ---------------------------------------------------------------------------
# _serialize_advice (private)
# ---------------------------------------------------------------------------


class TestSerializeAdvice:
    def test_serializes_advice_with_valid_json(self):
        advice = _make_advice(
            id=10,
            task_id=1,
            status="COMPLETED",
            topic_summary=json.dumps({"keyPoints": ["p1"]}),
            knowledge_fields=json.dumps([{"name": "kf"}]),
            learning_paths=json.dumps([{"stage": "s"}]),
            methodology=json.dumps([{"step": "m"}]),
            team_tips=json.dumps([{"role": "r"}]),
            updated_at=_NOW,
        )

        svc = _build_service()
        result = svc._serialize_advice(advice)

        assert result["id"] == 10
        assert result["taskId"] == 1
        assert result["status"] == "COMPLETED"
        assert result["topicSummary"] == {"keyPoints": ["p1"]}
        assert result["knowledgeFields"] == [{"name": "kf"}]
        assert result["learningPaths"] == [{"stage": "s"}]
        assert result["methodology"] == [{"step": "m"}]
        assert result["teamTips"] == [{"role": "r"}]
        assert result["updatedAt"] == _NOW.isoformat()

    def test_serializes_advice_with_invalid_json(self):
        advice = _make_advice(
            topic_summary="not valid json {{{",
            knowledge_fields="also bad",
            learning_paths=None,
            methodology="",
            team_tips=None,
            updated_at=None,
        )

        svc = _build_service()
        result = svc._serialize_advice(advice)

        assert result["topicSummary"] is None
        assert result["knowledgeFields"] is None
        assert result["learningPaths"] is None
        assert result["methodology"] is None
        assert result["teamTips"] is None
        assert result["updatedAt"] is None

    def test_serializes_advice_with_empty_string_fields(self):
        """Empty string blobs should return None via _loads."""
        advice = _make_advice(
            topic_summary="",
            knowledge_fields="",
            learning_paths="",
            methodology="",
            team_tips="",
        )

        svc = _build_service()
        result = svc._serialize_advice(advice)

        assert result["topicSummary"] is None
        assert result["knowledgeFields"] is None


# ---------------------------------------------------------------------------
# _safe_load (private)
# ---------------------------------------------------------------------------


class TestSafeLoad:
    def test_loads_valid_json(self):
        svc = _build_service()
        result = svc._safe_load('{"key": "value"}')
        assert result == {"key": "value"}

    def test_returns_none_for_none(self):
        svc = _build_service()
        assert svc._safe_load(None) is None

    def test_returns_none_for_empty_string(self):
        svc = _build_service()
        assert svc._safe_load("") is None

    def test_returns_none_for_invalid_json(self):
        svc = _build_service()
        assert svc._safe_load("not json!!!") is None

    def test_loads_json_array(self):
        svc = _build_service()
        result = svc._safe_load("[1, 2, 3]")
        assert result == [1, 2, 3]


# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------


class TestExceptionClasses:
    def test_llm_timeout_error(self):
        from app.domain.task.task_ai_advice_service import LLMTimeoutError

        err = LLMTimeoutError("timed out")
        assert str(err) == "timed out"

    def test_llm_model_error(self):
        from app.domain.task.task_ai_advice_service import LLMModelError

        err = LLMModelError("model error")
        assert str(err) == "model error"
