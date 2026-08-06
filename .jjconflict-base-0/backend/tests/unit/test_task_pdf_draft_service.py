import asyncio
import tempfile
from pathlib import Path

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


def test_validate_page_count_rejects_oversized_pdf() -> None:
    service = TaskPdfDraftService(llm_client=_FakeLLMClient("{}"), max_pages=20)

    with pytest.raises(BadRequestError, match="at most 20 pages"):
        service._validate_page_count(21)


@pytest.mark.anyio
async def test_pdf_generation_bounds_concurrency_and_stops_at_max_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = TaskPdfDraftService(
        llm_client=_FakeLLMClient("{}"),
        max_pages=20,
        max_concurrency=3,
    )
    active = 0
    max_active = 0
    processed_pages = 0

    temp_dir = tempfile.mkdtemp(prefix="pdf_test_")
    pages = [(f"page-{index}", {}) for index in range(12)]

    def fake_split(_pdf_bytes: bytes):
        return pages, temp_dir

    async def fake_generate(**kwargs):
        nonlocal active, max_active, processed_pages
        active += 1
        max_active = max(max_active, active)
        processed_pages += 1
        await asyncio.sleep(0.01)
        active -= 1
        return ([{"name": kwargs["text"], "intro": "i", "description": "d"}], 100)

    monkeypatch.setattr(service, "_split_pdf_to_pages", fake_split)
    monkeypatch.setattr(service, "generate_task_payloads_from_text", fake_generate)

    drafts, tokens = await service.generate_task_payloads_from_pdf(
        pdf_bytes=b"pdf",
        template={},
        space_id=1,
        category_id=None,
        forced_submitter_type=None,
        user_id=2,
        max_tasks=5,
    )

    assert len(drafts) == 5
    assert max_active == 3
    assert processed_pages == 6
    assert tokens == 600
    assert not Path(temp_dir).exists()
