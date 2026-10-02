import asyncio
import base64
import json
import tempfile
from pathlib import Path

import fitz
import httpx
import pytest

from app.core.config import settings
from app.core.errors import BadRequestError
from app.domain.gateway_chat import Completion, GatewayChat, Usage
from app.domain.task.task_pdf_draft_service import PageSource, TaskPdfDraftService


class _FakeChat:
    model = "fake-model"

    def __init__(self, content: str, *, usage: Usage | None = None) -> None:
        self._content = content
        self._usage = usage or Usage(prompt_tokens=600, completion_tokens=600)

    async def complete(self, **kwargs) -> Completion:
        _ = kwargs
        return Completion(self._content, self._usage)


class _RecordingChat:
    """假的草稿模型：记下每一次调用喂进来的提示词与图片。"""

    def __init__(self, model: str, content: str, *, usage: Usage | None = None) -> None:
        self.model = model
        self._content = content
        self._usage = usage or Usage(prompt_tokens=10, completion_tokens=5)
        self.prompts: list[str] = []
        self.images: list[list[bytes] | None] = []

    async def complete(self, *, prompt: str, images=None, **kwargs) -> Completion:
        _ = kwargs
        self.prompts.append(prompt)
        self.images.append(list(images) if images else None)
        return Completion(self._content, self._usage)


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _text_only_pdf(text: str = "Question 1: explain the segfault.") -> bytes:
    """一页有文字层的纸。"""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), text, fontsize=12)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _scanned_pdf() -> bytes:
    """一页扫描件：整页就是一张位图，没有文字层。"""
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 400, 520), 0)
    pixmap.set_rect(pixmap.irect, (240, 240, 240))
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(fitz.Rect(0, 0, 612, 792), stream=pixmap.tobytes("png"))
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _vector_only_pdf() -> bytes:
    """一页只有图形、没有文字层：``pymupdf4llm`` 会留下一个图片占位标记。"""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.draw_rect(fitz.Rect(72, 72, 500, 300), color=(0, 0, 0), width=2)
    page.draw_line(fitz.Point(72, 400), fitz.Point(500, 400))
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _scan_payload(name: str = "扫描题") -> str:
    return json.dumps(
        {"name": name, "intro": "来自扫描件", "description": "正文"},
        ensure_ascii=False,
    )


@pytest.mark.anyio
async def test_a_page_without_a_text_layer_is_rendered_and_read_as_an_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有文字层的页走兜底：整页渲染成 PNG，当图片发给能读图的模型。"""
    monkeypatch.setattr(settings, "task_draft_vision_models", {"vision-model"})
    chat = _RecordingChat("vision-model", _scan_payload())
    service = TaskPdfDraftService(chat=chat)  # type: ignore[arg-type]

    drafts, _, _ = await service.generate_task_payloads_from_pdf(
        pdf_bytes=_scanned_pdf(),
        template={},
        space_id=1,
        category_id=None,
        forced_submitter_type=None,
        user_id=2,
        max_tasks=1,
    )

    assert [d["name"] for d in drafts] == ["扫描题"]
    # 兜底真的把渲染出来的图片发了出去 —— 一张真的 PNG。
    assert len(chat.images) == 1
    sent = chat.images[0]
    assert sent is not None and len(sent) == 1
    assert sent[0].startswith(_PNG_MAGIC)


@pytest.mark.anyio
async def test_a_page_with_no_text_but_with_graphics_is_treated_as_a_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """判据是「没有文字层」，不是「markdown 为空」：只有图形的一页也得走兜底。

    ``pymupdf4llm`` 会给这种页留下一个 ``![](…)`` 占位标记（markdown 非空），但发
    进提示词的只是一句模型看不见的本地路径。若以 markdown 空为判据，这一页会被当成
    「有文字」而永远读不出东西。
    """
    monkeypatch.setattr(settings, "task_draft_vision_models", {"vision-model"})
    chat = _RecordingChat("vision-model", _scan_payload("图形页"))
    service = TaskPdfDraftService(chat=chat)  # type: ignore[arg-type]

    drafts, _, _ = await service.generate_task_payloads_from_pdf(
        pdf_bytes=_vector_only_pdf(),
        template={},
        space_id=1,
        category_id=None,
        forced_submitter_type=None,
        user_id=2,
        max_tasks=1,
    )

    assert [d["name"] for d in drafts] == ["图形页"]
    assert chat.images and chat.images[0] is not None


@pytest.mark.anyio
async def test_a_scan_the_model_still_cannot_read_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """兜底也读不出来时，错误要说清是扫描件这条路，而不是那句笼统的「读不出文字」。"""
    monkeypatch.setattr(settings, "task_draft_vision_models", {"vision-model"})
    chat = _RecordingChat("vision-model", "not-json")
    service = TaskPdfDraftService(chat=chat)  # type: ignore[arg-type]

    with pytest.raises(BadRequestError) as excinfo:
        await service.generate_task_payloads_from_pdf(
            pdf_bytes=_scanned_pdf(),
            template={},
            space_id=1,
            category_id=None,
            forced_submitter_type=None,
            user_id=2,
            max_tasks=1,
        )

    message = str(excinfo.value)
    assert "第 1 页" in message
    assert "扫描件" in message
    assert "渲染成图片" in message
    assert "Unable to extract readable text" not in message


@pytest.mark.anyio
async def test_a_scanned_page_never_goes_to_a_model_that_cannot_read_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读不了图的模型：一张图片都不发，当场说清换模型 —— 不是白花一次调用的钱。"""
    monkeypatch.setattr(settings, "task_draft_vision_models", {"vision-model"})
    chat = _RecordingChat("text-only-model", _scan_payload())
    service = TaskPdfDraftService(chat=chat)  # type: ignore[arg-type]

    with pytest.raises(BadRequestError) as excinfo:
        await service.generate_task_payloads_from_pdf(
            pdf_bytes=_scanned_pdf(),
            template={},
            space_id=1,
            category_id=None,
            forced_submitter_type=None,
            user_id=2,
            max_tasks=1,
        )

    message = str(excinfo.value)
    assert "text-only-model" in message
    assert "读不了图片" in message
    # 模型一次都没被叫到：图片没有发出去。
    assert chat.prompts == []
    assert chat.images == []


@pytest.mark.anyio
async def test_a_page_with_a_text_layer_is_never_sent_as_an_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有文字层的页照旧走文字那条路，一张图都不发。"""
    monkeypatch.setattr(settings, "task_draft_vision_models", {"vision-model"})
    chat = _RecordingChat("vision-model", _scan_payload("文字题"))
    service = TaskPdfDraftService(chat=chat)  # type: ignore[arg-type]

    drafts, _, _ = await service.generate_task_payloads_from_pdf(
        pdf_bytes=_text_only_pdf(),
        template={},
        space_id=1,
        category_id=None,
        forced_submitter_type=None,
        user_id=2,
        max_tasks=1,
    )

    assert [d["name"] for d in drafts] == ["文字题"]
    assert chat.images == [None]
    assert "explain the segfault" in chat.prompts[0]


def test_the_default_draft_model_is_opened_for_vision() -> None:
    """默认草稿模型（deepseek-flash）在部署里标了 supports_vision：兜底默认开着。"""
    assert "deepseek-flash" in settings.task_draft_vision_models


@pytest.mark.anyio
async def test_gateway_chat_sends_images_as_content_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GatewayChat`` 把图片拼成 OpenAI 形状的内容块（纯文本时仍是字符串）。"""
    monkeypatch.setattr(settings, "llm_gateway_admin_base", "http://gateway.test")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}], "usage": {}},
        )

    chat = GatewayChat(
        "key",
        "vision-model",
        max_tokens=16,
        transport=httpx.MockTransport(handler),
    )
    await chat.complete(
        system="sys",
        prompt="看这张图",
        timeout=5,
        images=[b"\x89PNG\r\n\x1a\n fake"],
    )

    content = seen["body"]["messages"][1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "看这张图"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == (
        "data:image/png;base64,"
        + base64.b64encode(b"\x89PNG\r\n\x1a\n fake").decode("ascii")
    )

    # 没有图片时还是那个纯字符串 —— 文字那条路一点没变。
    await chat.complete(system="sys", prompt="只有文字", timeout=5)
    assert seen["body"]["messages"][1]["content"] == "只有文字"


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
    pages = [PageSource(f"page-{index}", {}) for index in range(12)]

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
