from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import BadRequestError
from app.domain.llm.llm_client import LLMResponse
from app.domain.task.task_pdf_draft_service import TaskPdfDraftService


class _FakeLLMClient:
    def __init__(self, content: str, *, configured: bool = True, total_tokens: int = 1200) -> None:
        self.is_configured = configured
        self._content = content
        self._total_tokens = total_tokens

    async def get_completion(self, **kwargs) -> LLMResponse:
        _ = kwargs
        return LLMResponse(
            content=self._content,
            total_tokens=self._total_tokens,
            prompt_tokens=600,
            completion_tokens=600,
        )


@pytest.mark.anyio
async def test_generate_payload_from_text_merges_template_and_llm_result() -> None:
    llm = _FakeLLMClient(
        '{"task":[{"name":"AI 赛题","intro":"简述","description":"详细说明","defaultDeadline":"45","resubmittable":"false"}]}'
    )
    quota_service = SimpleNamespace(
        pre_check_and_reserve=AsyncMock(return_value=True),
        consume_tokens=AsyncMock(return_value=None),
    )
    service = TaskPdfDraftService(llm_client=llm, quota_service=quota_service)

    payload, tokens = await service.generate_task_payload_from_text(
        text="这是一个关于图像识别的赛题说明。",
        template={"editable": False, "submitterType": "TEAM", "minTeamSize": 2, "maxTeamSize": 5},
        space_id=7,
        category_id=9,
        forced_submitter_type=None,
        user_id=1001,
    )

    assert tokens == 1200
    assert payload["name"] == "AI 赛题"
    assert payload["space"] == 7
    assert payload["categoryId"] == 9
    assert payload["submitterType"] == "TEAM"
    assert payload["editable"] is False
    assert payload["resubmittable"] is False
    assert payload["defaultDeadline"] == 45
    assert payload["minTeamSize"] == 2
    assert payload["maxTeamSize"] == 5
    quota_service.pre_check_and_reserve.assert_awaited_once()
    quota_service.consume_tokens.assert_awaited_once()


@pytest.mark.anyio
async def test_generate_payload_from_text_respects_forced_submitter_type() -> None:
    llm = _FakeLLMClient(
        '{"task":[{"name":"比赛","intro":"介绍","description":"详情","submitterType":"USER"}]}'
    )
    service = TaskPdfDraftService(llm_client=llm, quota_service=None)

    payload, _ = await service.generate_task_payload_from_text(
        text="赛题文本",
        template={"submitterType": "USER"},
        space_id=1,
        category_id=None,
        forced_submitter_type="TEAM",
        user_id=2,
    )

    assert payload["submitterType"] == "TEAM"


@pytest.mark.anyio
async def test_generate_payload_from_text_requires_required_fields() -> None:
    llm = _FakeLLMClient('{"task":[{"intro":"只有介绍","description":"只有详情"}]}')
    service = TaskPdfDraftService(llm_client=llm, quota_service=None)

    with pytest.raises(BadRequestError, match="missing required field: name"):
        await service.generate_task_payload_from_text(
            text="赛题文本",
            template={},
            space_id=1,
            category_id=None,
            forced_submitter_type=None,
            user_id=2,
        )


@pytest.mark.anyio
async def test_generate_payload_from_text_rejects_invalid_llm_json() -> None:
    llm = _FakeLLMClient("not-json")
    service = TaskPdfDraftService(llm_client=llm, quota_service=None)

    with pytest.raises(BadRequestError, match="not valid JSON"):
        await service.generate_task_payload_from_text(
            text="赛题文本",
            template={},
            space_id=1,
            category_id=None,
            forced_submitter_type=None,
            user_id=2,
        )
