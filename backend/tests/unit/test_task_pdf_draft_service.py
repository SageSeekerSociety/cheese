import asyncio
import tempfile
from pathlib import Path

import pytest

from app.core.errors import BadRequestError
from app.domain.gateway_chat import Completion, Usage
from app.domain.task.task_pdf_draft_service import TaskPdfDraftService


class _FakeChat:
    model = "fake-model"

    def __init__(self, content: str, *, usage: Usage | None = None) -> None:
        self._content = content
        self._usage = usage or Usage(prompt_tokens=600, completion_tokens=600)

    async def complete(self, **kwargs) -> Completion:
        _ = kwargs
        return Completion(self._content, self._usage)


@pytest.mark.anyio
async def test_generate_payload_from_text_only_builds_content_draft() -> None:
    llm = _FakeChat(
        '{"name":"AI 赛题","intro":"简述","description":"详细说明","defaultDeadline":"45","resubmittable":"false"}'  # noqa: E501
    )
    service = TaskPdfDraftService(chat=llm)  # type: ignore[arg-type]

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
    llm = _FakeChat(
        '{"name":"比赛","intro":"介绍","description":"详情","submitterType":"USER"}'
    )
    service = TaskPdfDraftService(chat=llm)  # type: ignore[arg-type]

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
    llm = _FakeChat('{"intro":"只有介绍","description":"只有详情"}')
    service = TaskPdfDraftService(chat=llm)  # type: ignore[arg-type]

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
    llm = _FakeChat("not-json")
    service = TaskPdfDraftService(chat=llm)  # type: ignore[arg-type]

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
    service = TaskPdfDraftService(chat=_FakeChat("{}"), max_pages=20)  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match="at most 20 pages"):
        service._validate_page_count(21)


@pytest.mark.anyio
async def test_pdf_generation_bounds_concurrency_and_stops_at_max_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = TaskPdfDraftService(
        chat=_FakeChat("{}"),  # type: ignore[arg-type]
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

    drafts, tokens, illustrations = await service.generate_task_payloads_from_pdf(
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
    # 这几页没有图，报回来的插图就该是空的 —— 不是「有几页就报几张」。
    assert illustrations == []
    assert not Path(temp_dir).exists()


@pytest.mark.anyio
async def test_a_page_whose_answer_cannot_be_read_still_counts_as_spent() -> None:
    """模型答了、但答案读不出来的那一页，token 已经花了：它照样记进这次的花销。"""
    service = TaskPdfDraftService(  # type: ignore[arg-type]
        chat=_FakeChat(
            "not-json",
            usage=Usage(prompt_tokens=500, completion_tokens=40, cache_read_tokens=300),
        )
    )

    with pytest.raises(BadRequestError):
        await service.generate_task_payload_from_text(
            text="赛题文本",
            template={},
            space_id=1,
            category_id=None,
            forced_submitter_type=None,
            user_id=2,
        )
    assert service.spent == Usage(500, 40, 300, 0)
