from unittest.mock import AsyncMock, patch

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
        '{"task":{"name":"AI 赛题","intro":"简述","description":"详细说明","defaultDeadline":"45","resubmittable":"false"}}'
    )
    service = TaskPdfDraftService(llm_client=llm)

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


@pytest.mark.anyio
async def test_generate_payload_from_text_respects_forced_submitter_type() -> None:
    llm = _FakeLLMClient(
        '{"task":{"name":"比赛","intro":"介绍","description":"详情","submitterType":"USER"}}'
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

    assert payload["submitterType"] == "TEAM"


@pytest.mark.anyio
async def test_generate_payload_from_text_requires_required_fields() -> None:
    llm = _FakeLLMClient('{"task":{"intro":"只有介绍","description":"只有详情"}}')
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


def test_split_pdf_to_pages() -> None:
    import fitz

    doc = fitz.open()
    doc.new_page(width=612, height=792)
    doc.new_page(width=612, height=792)
    pdf_bytes = doc.tobytes()
    doc.close()

    pages = TaskPdfDraftService._split_pdf_to_pages(pdf_bytes)
    assert len(pages) == 2
    for page_bytes in pages:
        page_doc = fitz.open(stream=page_bytes, filetype="pdf")
        assert page_doc.page_count == 1
        page_doc.close()


def test_split_pdf_to_pages_single_page() -> None:
    import fitz

    doc = fitz.open()
    doc.new_page(width=612, height=792)
    pdf_bytes = doc.tobytes()
    doc.close()

    pages = TaskPdfDraftService._split_pdf_to_pages(pdf_bytes)
    assert len(pages) == 1


@pytest.mark.anyio
async def test_generate_single_task_from_page() -> None:
    llm = _FakeLLMClient('{"name":"单页赛题","intro":"单页介绍","description":"单页详细说明"}')
    service = TaskPdfDraftService(llm_client=llm)

    payload, tokens = await service._generate_single_task_from_page(
        markdown_text="# 单页赛题内容",
        template={},
        space_id=1,
        category_id=None,
        forced_submitter_type=None,
        default_topic_ids=None,
    )

    assert tokens == 1200
    assert payload["name"] == "单页赛题"
    assert payload["intro"] == "单页介绍"
    assert payload["description"] == "单页详细说明"
    assert payload["space"] == 1


@pytest.mark.anyio
async def test_generate_single_task_from_page_empty_content() -> None:
    llm = _FakeLLMClient("{}")
    service = TaskPdfDraftService(llm_client=llm)

    with pytest.raises(BadRequestError, match="Page content is empty"):
        await service._generate_single_task_from_page(
            markdown_text="   ",
            template={},
            space_id=1,
            category_id=None,
            forced_submitter_type=None,
        )


@pytest.mark.anyio
async def test_generate_task_payloads_from_pdf_parallel() -> None:
    import fitz

    doc = fitz.open()
    doc.new_page(width=612, height=792)
    doc.new_page(width=612, height=792)
    pdf_bytes = doc.tobytes()
    doc.close()

    llm = _FakeLLMClient('{"name":"并行赛题","intro":"并行介绍","description":"并行详情"}')
    service = TaskPdfDraftService(llm_client=llm)

    with patch.object(service, "_upload_and_replace_images", new_callable=AsyncMock) as mock_upload:
        mock_upload.return_value = "uploaded description"

        payloads, total_tokens = await service.generate_task_payloads_from_pdf(
            pdf_bytes=pdf_bytes,
            template={},
            space_id=1,
            category_id=None,
            forced_submitter_type=None,
            user_id=1,
        )

    assert len(payloads) == 2
    assert total_tokens == 2400
    for payload in payloads:
        assert payload["name"] == "并行赛题"
        assert payload["description"] == "uploaded description"
    assert mock_upload.call_count == 2


@pytest.mark.anyio
async def test_generate_task_payloads_from_pdf_empty_pdf() -> None:
    service = TaskPdfDraftService()

    with pytest.raises(BadRequestError, match="Uploaded PDF is empty"):
        await service.generate_task_payloads_from_pdf(
            pdf_bytes=b"",
            template={},
            space_id=1,
            category_id=None,
            forced_submitter_type=None,
            user_id=1,
        )
