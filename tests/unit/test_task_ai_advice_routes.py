from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.api.routes.tasks import get_task_ai_advice_service
from app.common.auth import get_optional_user_id
from app.main import app


class _QuotaInfo:
    def __init__(self, *, remaining: float, total: float, reset_time: datetime) -> None:
        self.remaining = remaining
        self.total = total
        self.reset_time = reset_time


class _StubTaskAIAdviceService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create_conversation(
        self,
        *,
        task_id: int,
        user_id: int,
        question: str,
        parent_id: int | None,
        context: dict | None,
        conversation_id: str | None = None,
    ) -> tuple[dict, _QuotaInfo]:
        self.calls.append(
            {
                "task_id": task_id,
                "user_id": user_id,
                "question": question,
                "parent_id": parent_id,
                "context": context,
                "conversation_id": conversation_id,
            }
        )
        payload = {"conversation": {"conversationId": conversation_id or "conv", "messages": []}}
        quota = _QuotaInfo(
            remaining=4.0,
            total=10.0,
            reset_time=datetime(2025, 12, 2, tzinfo=timezone.utc),
        )
        return payload, quota


@pytest.mark.anyio
async def test_create_ai_advice_conversation_forwards_context(python_client):
    service = _StubTaskAIAdviceService()

    async def _service_override():
        return service

    async def _user_override():
        return 99

    app.dependency_overrides[get_task_ai_advice_service] = _service_override
    app.dependency_overrides[get_optional_user_id] = _user_override

    try:
        payload = {
            "question": "   请帮我拆解   ",
            "parentId": 123,
            "conversationId": "existing",
            "context": {"section": "knowledge_fields", "sectionIndex": 2},
        }
        resp = await python_client.post("/tasks/42/ai-advice/conversations", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["conversation"]["conversationId"] == "existing"
        assert body["data"]["quota"] == {
            "remaining": 4.0,
            "total": 10.0,
            "reset_time": "2025-12-02T00:00:00+00:00",
        }
    finally:
        app.dependency_overrides.clear()

    assert service.calls, "service should be invoked"
    call = service.calls[0]
    assert call["task_id"] == 42
    assert call["user_id"] == 99
    # question should be trimmed before invoking the service
    assert call["question"] == "请帮我拆解"
    assert call["parent_id"] == 123
    assert call["context"] == {"section": "knowledge_fields", "sectionIndex": 2}
    assert call["conversation_id"] == "existing"


@pytest.mark.anyio
async def test_create_ai_advice_conversation_missing_question_returns_400(python_client):
    service = _StubTaskAIAdviceService()

    async def _service_override():
        return service

    async def _user_override():
        return 1

    app.dependency_overrides[get_task_ai_advice_service] = _service_override
    app.dependency_overrides[get_optional_user_id] = _user_override

    try:
        resp = await python_client.post(
            "/tasks/1/ai-advice/conversations",
            json={"question": "   "},
        )
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.clear()

    assert not service.calls
