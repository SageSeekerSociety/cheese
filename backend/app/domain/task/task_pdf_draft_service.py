import asyncio
import io as _io
import json
import logging
import os
import pathlib
import re
import shutil
import tempfile
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast

import fitz
import pymupdf4llm

from app.core.config import settings
from app.core.errors import BadRequestError, QuotaExceededError, SystemBusyError
from app.core.sentences import exception_text, say
from app.core.storage import (
    compute_file_hash,
    generate_storage_key,
    get_storage_backend,
)
from app.domain.gateway_chat import GatewayCallError, GatewayChat, Usage
from app.domain.service_keys import KeySpec, service_key
from app.domain.usage.ledger import Ledger, RateRow, Rates, payer_for_person

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadedIllustration:
    """一张抽出来的插图：已经躺在存储上，等着被登记成发布者名下的附件。

    生成草稿这一步只把它写进存储（题干里的图片链接指的就是它），**落成附件行**是
    预览路由的事 —— 只有那一层有数据库会话。所以这里把「登记一行附件」要用的每
    一项都带出去，而不是只给一个 URL。``url`` 是存储给的那一份（相对地址），题干
    里内联的是它加上站点前缀的绝对地址。
    """

    filename: str
    content_type: str
    storage_key: str
    url: str
    size: int
    file_hash: str


@dataclass(frozen=True)
class PageSource:
    """一页纸的两种读法。

    有文字层的页，``markdown`` 是 ``pymupdf4llm`` 读出来的正文（``images`` 是它
    顺手写出来的插图文件）。没有文字层的扫描页 ``markdown`` 为空、``scan_png`` 是
    整页渲染出来的 PNG：这条兜底把它当图片发给能读图的模型，而不是像以前那样直接
    报「这页读不出文字」把整份 PDF 打回。
    """

    markdown: str
    images: dict[str, str]
    scan_png: bytes | None = None


def task_draft_key_spec() -> KeySpec:
    """The gateway key drafts are made on. No budget of its own: each preview
    is paid for from the publisher's personal credits."""
    return KeySpec(
        name="task-draft-gateway-key",
        alias="task-draft",
        model=settings.task_draft_model,
        budget_usd=None,
        rpm=120,
    )


RateTable = Mapping[str, RateRow]
_NOT_OPEN = say("pdfDraftNotOpen")

# 整页渲染的清晰度，与读文档那条路（backend/sandbox/skills/documents/scripts/
# read.py 的 ``--render``）同档：够模型看清正文，又不至于把一页顶到几十 MB。
_RENDER_DPI = 150


def _scanned_page_without_vision(page_number: int, model: str) -> str:
    """这页没有文字层，而当前模型读不了图 —— 兜底根本没机会跑。"""
    return say("pdfScannedPageNoVision", page=page_number, model=model)


def _scan_fallback_failed(page_number: int, detail: str) -> str:
    """渲染后模型也没读出内容 —— 说清是这条兜底路自己没读出来。"""
    return say("pdfScanFallbackFailed", page=page_number, detail=detail)


class TaskPdfDraftService:
    """Generate task payload from a PDF document via the model on ``chat``.

    ``spent`` adds up every call the model answered, drafts or not: a page whose
    answer could not be read still cost its tokens, and whoever asked pays for
    them.
    """

    def __init__(
        self,
        *,
        chat: GatewayChat,
        rate_table: RateTable | None = None,
        timeout_seconds: float | None = None,
        max_pages: int | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        self._chat = chat
        self._rate_table = rate_table
        self.spent = Usage()
        self.model = chat.model
        self._timeout_seconds = timeout_seconds or settings.task_draft_timeout_s
        self._max_pages = (
            max_pages if max_pages is not None else settings.pdf_import_max_pages
        )
        self._max_concurrency = (
            max_concurrency
            if max_concurrency is not None
            else settings.pdf_import_max_concurrency
        )

    @classmethod
    async def on_gateway(
        cls, db: Any, rate_table: RateTable | None
    ) -> "TaskPdfDraftService":
        key = await service_key(db, task_draft_key_spec())
        if key is None:
            raise SystemBusyError(_NOT_OPEN)
        return cls(
            rate_table=rate_table,
            chat=GatewayChat(
                key,
                settings.task_draft_model,
                max_tokens=settings.task_draft_max_tokens,
            ),
        )

    @asynccontextmanager
    async def charged_to(self, db: Any, user_id: int) -> AsyncIterator[None]:
        """Admit a preview the user asked for, then charge what it spent.

        Every page the model answered was paid for, drafts or not; the charge
        is committed on the way out so a preview that fails afterwards still
        leaves it.
        """
        rates = Rates.of(self.model, self._rate_table)
        if rates is None:
            raise SystemBusyError(_NOT_OPEN)
        ledger = Ledger(db)
        payer = await payer_for_person(db, user_id)
        refused = await ledger.admit(payer)
        if refused is not None:
            raise QuotaExceededError(refused.message)
        try:
            yield
        finally:
            spent = self.spent
            if spent.total_tokens:
                await ledger.charge_priced(
                    payer,
                    user_id=user_id,
                    model=self.model,
                    rates=rates,
                    input_tokens=spent.prompt_tokens,
                    output_tokens=spent.completion_tokens,
                    cache_read_tokens=spent.cache_read_tokens,
                    cache_write_tokens=spent.cache_write_tokens,
                    kind="task_pdf_draft",
                )
            await db.commit()

    @staticmethod
    def pick_template(
        task_templates: list[Any], template_index: int = 0
    ) -> dict[str, Any]:
        if not task_templates:
            return {}
        if template_index < 0 or template_index >= len(task_templates):
            return {}
        selected = task_templates[template_index]
        if isinstance(selected, dict):
            return selected
        return {}

    async def generate_task_payload_from_pdf(
        self,
        *,
        pdf_bytes: bytes,
        template: dict[str, Any],
        space_id: int,
        category_id: int | None,
        forced_submitter_type: str | None,
        user_id: int,
        default_topic_ids: list[int] | None = None,
    ) -> tuple[dict[str, Any], int]:
        """Convenience wrapper: return a single task payload from a PDF."""
        payloads, token_used, _ = await self.generate_task_payloads_from_pdf(
            pdf_bytes=pdf_bytes,
            template=template,
            space_id=space_id,
            category_id=category_id,
            forced_submitter_type=forced_submitter_type,
            user_id=user_id,
            default_topic_ids=default_topic_ids,
            max_tasks=1,
        )
        if not payloads:
            raise BadRequestError("No task payload extracted from PDF")
        return payloads[0], token_used

    def _validate_page_count(self, page_count: int) -> None:
        if page_count == 0:
            raise BadRequestError("PDF has no pages")
        if page_count > self._max_pages:
            raise BadRequestError(f"PDF can contain at most {self._max_pages} pages")

    def _split_pdf_to_pages(self, pdf_bytes: bytes) -> tuple[list[PageSource], str]:

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_count = doc.page_count

        try:
            self._validate_page_count(page_count)

            temp_dir = tempfile.mkdtemp(prefix="pdf_extract_")
            try:
                tmp_path = os.path.join(temp_dir, "input.pdf")
                with open(tmp_path, "wb") as f:
                    f.write(pdf_bytes)

                page_data: list[PageSource] = []
                for page_num in range(page_count):
                    page_images_dir = pathlib.Path(temp_dir) / f"images_p{page_num}"
                    page_images_dir.mkdir(exist_ok=True)

                    # pymupdf4llm.to_markdown is an untyped wrapper whose inferred
                    # return is str | list[dict] (the list only in page_chunks mode).
                    # With page_chunks unset (default False) it always returns str.
                    markdown_text = cast(
                        str,
                        pymupdf4llm.to_markdown(
                            tmp_path,
                            pages=[page_num],
                            use_ocr=False,
                            write_images=True,
                            image_path=str(page_images_dir),
                            image_format="png",
                        ),
                    )

                    scan_png = self._scanned_page_png(doc, page_num, markdown_text)

                    image_map: dict[str, str] = {}
                    if page_images_dir.exists():
                        for img_file in page_images_dir.iterdir():
                            if img_file.is_file():
                                image_map[img_file.name] = str(img_file)

                    page_data.append(
                        PageSource(markdown_text or "", image_map, scan_png)
                    )

                return page_data, temp_dir
            except BaseException:
                if os.path.isdir(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
                raise
        finally:
            doc.close()

    def _scanned_page_png(
        self, doc: Any, page_num: int, markdown_text: str
    ) -> bytes | None:
        """这一页要当图片发给模型吗？要，就渲染成 PNG 交出去。

        判据是「这一页没有文字层」，而不是「``pymupdf4llm`` 读出的 markdown 为空」：
        一页只有图形、没有文字时，``pymupdf4llm`` 会把整页渲染成一张图并只留下一个
        ``![](…)`` 占位标记 —— markdown 非空，可发进提示词的却是一句模型看不见的本地
        路径。所以以 ``get_text()`` 探文字层为准，markdown 空也一并算作读不出。

        模型读不了图时**早早地在这里失败**：这页注定读不出，与其把整份 PDF 拖到逐页
        调用那一步、再让它淹没在「所有页都失败」里，不如当场说清是哪一页、为什么。
        """
        page_text = doc.load_page(page_num).get_text()
        if page_text.strip() and markdown_text.strip():
            return None
        if not self._model_reads_images():
            raise BadRequestError(
                _scanned_page_without_vision(page_num + 1, self.model)
            )
        return doc.load_page(page_num).get_pixmap(dpi=_RENDER_DPI).tobytes("png")

    def _model_reads_images(self) -> bool:
        """这一版的草稿模型读不读图。

        默认草稿模型是文本模型 —— 一份 PNG 发给读不了图的模型只是白白花一次调用的
        钱，所以只有部署在 ``task_draft_vision_models`` 里点过名的模型才走兜底。
        """
        return self.model in settings.task_draft_vision_models

    async def generate_task_payloads_from_pdf(
        self,
        *,
        pdf_bytes: bytes,
        template: dict[str, Any],
        space_id: int,
        category_id: int | None,
        forced_submitter_type: str | None,
        user_id: int,
        default_topic_ids: list[int] | None = None,
        max_tasks: int = 20,
    ) -> tuple[list[dict[str, Any]], int, list[UploadedIllustration]]:
        """按页读出草稿。第三个返回值是**真的进了某条草稿题干**的那些插图。"""
        if not pdf_bytes:
            raise BadRequestError("Uploaded PDF is empty")
        if max_tasks < 1:
            raise BadRequestError("maxTasks must be at least 1")

        page_data, temp_dir = await asyncio.to_thread(
            self._split_pdf_to_pages, pdf_bytes
        )
        try:

            async def process_page(
                page_source: PageSource, page_number: int
            ) -> tuple[list[dict[str, Any]], int]:
                if page_source.scan_png is not None:
                    (
                        payloads,
                        token_used,
                    ) = await self.generate_task_payloads_from_page_image(
                        image=page_source.scan_png,
                        page_number=page_number,
                        template=template,
                        space_id=space_id,
                        category_id=category_id,
                        forced_submitter_type=forced_submitter_type,
                        user_id=user_id,
                        default_topic_ids=default_topic_ids,
                    )
                else:
                    payloads, token_used = await self.generate_task_payloads_from_text(
                        text=page_source.markdown,
                        template=template,
                        space_id=space_id,
                        category_id=category_id,
                        forced_submitter_type=forced_submitter_type,
                        user_id=user_id,
                        default_topic_ids=default_topic_ids,
                    )

                return payloads, token_used

            all_payloads: list[dict[str, Any]] = []
            illustrations: list[UploadedIllustration] = []
            total_tokens = 0
            failed_pages: list[int] = []
            failure_reasons: list[str] = []
            attempted_pages = 0
            for batch_start in range(0, len(page_data), self._max_concurrency):
                batch = page_data[batch_start : batch_start + self._max_concurrency]
                results = await asyncio.gather(
                    *[
                        process_page(page_source, batch_start + offset + 1)
                        for offset, page_source in enumerate(batch)
                    ],
                    return_exceptions=True,
                )
                attempted_pages += len(batch)

                for offset, result in enumerate(results):
                    page_index = batch_start + offset
                    if isinstance(result, BaseException):
                        failed_pages.append(page_index + 1)
                        failure_reasons.append(str(result))
                        logger.warning(
                            "PDF page %d LLM processing failed: %s",
                            page_index + 1,
                            result,
                        )
                        continue

                    payloads, tokens = result
                    total_tokens += tokens
                    remaining = max_tasks - len(all_payloads)
                    selected_payloads = payloads[:remaining]
                    image_map = page_data[page_index].images
                    for payload in selected_payloads:
                        if payload.get("description") and image_map:
                            (
                                payload["description"],
                                page_illustrations,
                            ) = await self._upload_and_replace_images(
                                markdown_text=payload["description"],
                                image_map=image_map,
                            )
                            illustrations.extend(page_illustrations)
                    all_payloads.extend(selected_payloads)

                if len(all_payloads) >= max_tasks:
                    break

            if not all_payloads:
                # 每一页为什么失败都带上：扫描页那条兜底路自己没读出来时，正是靠
                # 这里的那句话（第 N 页是扫描件…）而不是一句笼统的「所有页都失败」
                # 让调用方知道下一步该怎么办。
                raise BadRequestError(
                    self._all_pages_failed_message(attempted_pages, failure_reasons)
                )

            if failed_pages:
                logger.warning(
                    "PDF processing: %d/%d pages succeeded, failed pages: %s",
                    attempted_pages - len(failed_pages),
                    attempted_pages,
                    failed_pages,
                )

            logger.info(
                "PDF import completed: user_id=%d pages=%d drafts=%d tokens=%d",
                user_id,
                attempted_pages,
                len(all_payloads),
                total_tokens,
            )

            return all_payloads, total_tokens, illustrations
        finally:
            if os.path.isdir(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    async def generate_task_payloads_from_text(
        self,
        *,
        text: str,
        template: dict[str, Any],
        space_id: int,
        category_id: int | None,
        forced_submitter_type: str | None,
        user_id: int,
        default_topic_ids: list[int] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        normalized_text = text.strip()
        if not normalized_text:
            raise BadRequestError("PDF content is empty or unreadable")
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(text=normalized_text, template=template)

        try:
            response = await self._chat.complete(
                system=system_prompt,
                prompt=user_prompt,
                json_response=True,
                timeout=self._timeout_seconds,
            )
        except GatewayCallError as exc:
            raise BadRequestError(exception_text(exc)) from exc
        self.spent += response.usage

        parsed = self._parse_llm_json(response.content)
        candidates = self._extract_task_candidates(parsed)
        payloads = [
            self._normalize_task_payload(
                llm_result=candidate,
                template=template,
                forced_submitter_type=forced_submitter_type,
                space_id=space_id,
                category_id=category_id,
                default_topic_ids=default_topic_ids,
            )
            for candidate in candidates
        ]

        return payloads, response.usage.total_tokens

    async def generate_task_payloads_from_page_image(
        self,
        *,
        image: bytes,
        page_number: int,
        template: dict[str, Any],
        space_id: int,
        category_id: int | None,
        forced_submitter_type: str | None,
        user_id: int,
        default_topic_ids: list[int] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """一页没有文字层时的兜底：把整页渲染图当图片发给模型。

        与文字那条路的差别只有「喂进去的是图片而不是 markdown」。读不出来时抛出的
        错误要说清是这条兜底路自己没读出来（第 N 页是扫描件、渲染后模型也没读出来），
        而不是回到那句笼统的「读不出文字」—— 后者正是这条路要消灭的东西。
        """
        # ``_scanned_page_png`` 已经拦下读不了图的模型；这里再断言一次，让这个方法
        # 直接调用时也守得住「绝不把图片发给读不了图的模型」这条线。
        if not self._model_reads_images():
            raise BadRequestError(_scanned_page_without_vision(page_number, self.model))

        try:
            response = await self._chat.complete(
                system=self._build_system_prompt(),
                prompt=self._build_scan_user_prompt(),
                images=[image],
                json_response=True,
                timeout=self._timeout_seconds,
            )
        except GatewayCallError as exc:
            raise BadRequestError(
                _scan_fallback_failed(page_number, exception_text(exc))
            ) from exc
        self.spent += response.usage

        try:
            parsed = self._parse_llm_json(response.content)
            candidates = self._extract_task_candidates(parsed)
            payloads = [
                self._normalize_task_payload(
                    llm_result=candidate,
                    template=template,
                    forced_submitter_type=forced_submitter_type,
                    space_id=space_id,
                    category_id=category_id,
                    default_topic_ids=default_topic_ids,
                )
                for candidate in candidates
            ]
        except BadRequestError as exc:
            raise BadRequestError(
                _scan_fallback_failed(page_number, exception_text(exc))
            ) from exc

        return payloads, response.usage.total_tokens

    async def generate_task_payload_from_text(
        self,
        *,
        text: str,
        template: dict[str, Any],
        space_id: int,
        category_id: int | None,
        forced_submitter_type: str | None,
        user_id: int,
        default_topic_ids: list[int] | None = None,
    ) -> tuple[dict[str, Any], int]:
        """Convenience wrapper: return a single task payload from text."""
        payloads, token_used = await self.generate_task_payloads_from_text(
            text=text,
            template=template,
            space_id=space_id,
            category_id=category_id,
            forced_submitter_type=forced_submitter_type,
            user_id=user_id,
            default_topic_ids=default_topic_ids,
        )
        if not payloads:
            raise BadRequestError("No task payload extracted from text")
        return payloads[0], token_used

    async def _upload_and_replace_images(
        self,
        *,
        markdown_text: str,
        image_map: dict[str, str],
    ) -> tuple[str, list[UploadedIllustration]]:
        """把抽出的插图传上存储，并把正文里的本地引用换成它的地址。

        返回换好地址的 Markdown，以及**这次真的传上去了**的那些插图。正文里没引用
        的图不传：题干里没有它，就没有人看得见它，把它登记成一道题名下的附件只会让
        屏幕上那句「抽出的插图 N 张」跟真的数得到的张数对不上。
        """
        if not image_map:
            return markdown_text, []

        storage = get_storage_backend()
        replaced = markdown_text
        uploaded: list[UploadedIllustration] = []

        for filename, local_path in image_map.items():
            # Replace image references in markdown:
            #   ![alt](images/filename)  or  ![](images/filename)
            escaped_name = re.escape(filename)
            pattern = r"!\[([^\]]*)\]\([^)]*" + escaped_name + r"\)"
            if not re.search(pattern, replaced):
                continue
            if not await asyncio.to_thread(os.path.isfile, local_path):
                continue

            storage_key = generate_storage_key(filename, prefix="task-images")
            content = await asyncio.to_thread(pathlib.Path(local_path).read_bytes)

            buffer = _io.BytesIO(content)
            # 摘要先算：``compute_file_hash`` 读完会 seek 回开头，接着上传的就是同一
            # 份字节，不必为了算摘要再读一遍文件、也不必多留一份副本。
            file_hash = compute_file_hash(buffer)
            storage_url = await storage.upload(buffer, storage_key, "image/png")

            # Convert relative storage URL to absolute URL pointing to the backend
            # storage_url is like "/uploads/task-images/2026/05/01/abc.png"
            # We prepend the backend base URL so the frontend can load images
            backend_base = settings.avatar_base_url.rstrip("/")
            url = f"{backend_base}{storage_url}"

            replacement = r"![\1](" + url + ")"
            replaced = re.sub(pattern, replacement, replaced)
            uploaded.append(
                UploadedIllustration(
                    filename=filename,
                    content_type="image/png",
                    storage_key=storage_key,
                    url=storage_url,
                    size=len(content),
                    file_hash=file_hash,
                )
            )

        return replaced, uploaded

    def _build_system_prompt(self) -> str:
        return (
            "你是比赛运营专家。你将收到从 PDF 单页提取的 Markdown 文本，"
            "文本中可能包含排版错乱、多余换行、标题层级错误等问题，"
            "也可能包含图片占位标记（如 `![描述](images/xxx.png)`）。"
            "你的任务是：\n"
            "1. 修正 Markdown 的排版格式，使其结构清晰、层级正确、可读性强；\n"
            "2. **必须保留所有图片占位标记**，不要删除或修改它们；\n"
            "3. 可以将图片中提取的文本（picture text部分）删除；\n"
            "4. 将修正后的 Markdown 放入 JSON 的 `description` 字段；\n"
            "5. 从内容中提炼出合适的 `name`（赛题名称）和 `intro`（简短介绍）。\n\n"
            "**输出格式要求**：直接输出一个 JSON 对象，包含 name、intro、description 三个字段。"  # noqa: E501
            "形如 "
            '{"name": "...", "intro": "...", "description": "..."}。\n\n'
            "**重要：只输出纯 JSON，不要用 ```json 代码块包裹，不要加任何前缀或后缀说明。**"  # noqa: E501
        )

    def _build_user_prompt(self, *, text: str, template: dict[str, Any]) -> str:
        clipped = text[:12000]
        return (
            "下面是从 PDF 单页中提取的 Markdown 文本（可能包含图片占位标记如 "
            "`![描述](images/xxx.png)`，请务必保留这些标记）：\n"
            f"{clipped}\n\n"
            "请基于以上内容生成一个 JSON 对象，"
            "其中 `description` 字段放置修正排版后的完整 Markdown。"
        )

    def _build_scan_user_prompt(self) -> str:
        """扫描页那条兜底路喂给模型的话：图片就在这一轮里，直接读。"""
        return (
            "这一页没有文字层，是一张扫描/图片页。附上的图片就是这一页的整页渲染，"
            "请直接阅读图片内容，按图片里的正文整理出一份 Markdown。\n"
            "请基于图片内容生成一个 JSON 对象，"
            "其中 `description` 字段放置整理排版后的完整 Markdown。"
        )

    @staticmethod
    def _all_pages_failed_message(attempted_pages: int, reasons: list[str]) -> str:
        base = f"All {attempted_pages} attempted page(s) failed to process"
        detail = "; ".join(reason for reason in reasons if reason)
        return f"{base}: {detail}" if detail else base

    def _parse_llm_json(self, content: str) -> dict[str, Any]:
        # Strip markdown code fences if present (e.g. ```json ... ```)
        stripped = self._strip_markdown_code_fence(content)

        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as exc:
            preview = stripped[:800] if len(stripped) > 800 else stripped
            raise BadRequestError(
                f"LLM response is not valid JSON. Content preview: {preview}..."
            ) from exc
        if not isinstance(obj, dict):
            raise BadRequestError(
                f"LLM response JSON must be an object, got {type(obj).__name__}."
            )
        return obj

    @staticmethod
    def _strip_markdown_code_fence(text: str) -> str:
        """Remove surrounding markdown code fences (```json ... ```) from text."""
        stripped = text.strip()
        # Pattern: optional ```json or ``` at start, content, ``` at end
        match = re.match(
            r"^```(?:json|JSON)?\s*\n(.*?)\n```\s*$",
            stripped,
            re.DOTALL,
        )
        if match:
            return match.group(1)
        # Try without newline: ```json{...}```
        if stripped.startswith("```") and stripped.endswith("```"):
            inner = stripped[3:-3].strip()
            if inner.lower().startswith("json"):
                inner = inner[4:].strip()
            return inner
        return text

    def _extract_task_candidates(self, parsed: dict[str, Any]) -> list[dict[str, Any]]:
        if "tasks" in parsed and isinstance(parsed["tasks"], list):
            candidates = [item for item in parsed["tasks"] if isinstance(item, dict)]
            if candidates:
                return candidates

        if "name" in parsed or "intro" in parsed or "description" in parsed:
            return [parsed]

        raise BadRequestError(
            "LLM response must contain name/intro/description fields. "
            f"Got keys: {list(parsed.keys()) if parsed else 'empty'}"
        )

    def _normalize_task_payload(
        self,
        *,
        llm_result: dict[str, Any],
        template: dict[str, Any],
        forced_submitter_type: str | None,
        space_id: int,
        category_id: int | None,
        default_topic_ids: list[int] | None,
    ) -> dict[str, Any]:
        """Build the final task draft payload.

        Only name, intro, description come from the AI result (with optional
        template fallback). Publishing parameters are intentionally not filled
        here; the PDF flow applies the regular task form values when drafts are
        confirmed.
        """
        # --- Core fields: AI output, with template as fallback ---
        template_defaults = self._extract_template_defaults(template)
        name = str(
            llm_result.get("name") or template_defaults.get("name") or ""
        ).strip()
        intro = str(
            llm_result.get("intro") or template_defaults.get("intro") or ""
        ).strip()
        description = str(
            llm_result.get("description") or template_defaults.get("description") or ""
        ).strip()

        if not name:
            raise BadRequestError("LLM output missing required field: name")
        if not intro:
            raise BadRequestError("LLM output missing required field: intro")
        if not description:
            raise BadRequestError("LLM output missing required field: description")

        payload: dict[str, Any] = {
            "name": name,
            "intro": intro,
            "description": description,
            "space": space_id,
        }

        if category_id is not None:
            payload["categoryId"] = category_id

        return payload

    def _extract_template_defaults(self, template: dict[str, Any]) -> dict[str, Any]:
        """Extract only core fields from the template as fallback values.

        Only name, intro, description are relevant — all other fields are
        filled by the system and never read from templates.
        """
        if "task" in template and isinstance(template["task"], dict):
            source = template["task"]
        else:
            source = template
        allowed_keys = {"name", "intro", "description"}
        return {k: v for k, v in source.items() if k in allowed_keys}
