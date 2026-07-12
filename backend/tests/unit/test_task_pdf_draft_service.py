import pytest

from app.core.errors import BadRequestError
from app.domain.llm.llm_client import LLMResponse
from app.domain.task.task_pdf_draft_service import TaskPdfDraftService


class _FakeLLMClient:
    def __init__(
        self, content: str, *, configured: bool = True, total_tokens: int = 1200
    ) -> None:
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
async def test_generate_payload_from_text_only_builds_content_draft() -> None:
    llm = _FakeLLMClient(
        '{"name":"AI 赛题","intro":"简述","description":"详细说明","defaultDeadline":"45","resubmittable":"false"}'  # noqa: E501
    )
    service = TaskPdfDraftService(llm_client=llm)

    payload, tokens = await service.generate_task_payload_from_text(
        text="这是一个关于图像识别的赛题说明。",
        template={
            "editable": False,
            "submitterType": "TEAM",
            "minTeamSize": 2,
            "maxTeamSize": 5,
        },
        space_id=7,
        category_id=9,
        forced_submitter_type=None,
        user_id=1001,
    )

    assert tokens == 1200
    assert payload["name"] == "AI 赛题"
    assert payload["intro"] == "简述"
    assert payload["description"] == "详细说明"
    assert payload["space"] == 7
    assert payload["categoryId"] == 9
    assert "submitterType" not in payload
    assert "editable" not in payload
    assert "resubmittable" not in payload
    assert "defaultDeadline" not in payload
    assert "minTeamSize" not in payload
    assert "maxTeamSize" not in payload


@pytest.mark.anyio
async def test_generate_payload_from_text_ignores_publish_parameters() -> None:
    llm = _FakeLLMClient(
        '{"name":"比赛","intro":"介绍","description":"详情","submitterType":"USER"}'
    )
    service = TaskPdfDraftService(llm_client=llm)

    payload, _ = await service.generate_task_payload_from_text(
        text="赛题文本",
        template={"submitterType": "USER"},
        space_id=1,
        category_id=None,
        forced_submitter_type="TEAM",
        user_id=2,
    )

    assert payload == {
        "name": "比赛",
        "intro": "介绍",
        "description": "详情",
        "space": 1,
    }


@pytest.mark.anyio
async def test_generate_payload_from_text_requires_required_fields() -> None:
    llm = _FakeLLMClient('{"intro":"只有介绍","description":"只有详情"}')
    service = TaskPdfDraftService(llm_client=llm)

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
    service = TaskPdfDraftService(llm_client=llm)

    with pytest.raises(BadRequestError, match="not valid JSON"):
        await service.generate_task_payload_from_text(
            text="赛题文本",
            template={},
            space_id=1,
            category_id=None,
            forced_submitter_type=None,
            user_id=2,
        )
