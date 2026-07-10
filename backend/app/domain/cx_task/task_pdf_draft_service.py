import asyncio
import json
import logging
import os
import pathlib
import re
import shutil
import tempfile
from typing import Any

import pymupdf4llm

from app.core.config import settings
from app.core.errors import BadRequestError
from app.core.storage import generate_storage_key, get_storage_backend
from app.domain.llm.llm_client import LLMAPIError, LLMClient, LLMConnectionError, LLMTimeoutError

logger = logging.getLogger(__name__)


class TaskPdfDraftService:
    """Generate task payload from a PDF document via LLM."""

    def __init__(
        self,
        *,
        llm_client: LLMClient | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._llm_client = llm_client or LLMClient()
        self._timeout_seconds = timeout_seconds or settings.openai_pdf_timeout_seconds

    @staticmethod
    def pick_template(task_templates: list[Any], template_index: int = 0) -> dict[str, Any]:
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
        payloads, token_used = await self.generate_task_payloads_from_pdf(
            pdf_bytes=pdf_bytes,
            template=template,
            space_id=space_id,
            category_id=category_id,
            forced_submitter_type=forced_submitter_type,
            user_id=user_id,
            default_topic_ids=default_topic_ids,
        )
        if not payloads:
            raise BadRequestError("No task payload extracted from PDF")
        return payloads[0], token_used

    def _split_pdf_to_pages(self, pdf_bytes: bytes) -> tuple[list[tuple[str, dict[str, str]]], str]:
        import fitz

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_count = doc.page_count
        doc.close()

        if page_count == 0:
            raise BadRequestError("PDF has no pages")

        temp_dir = tempfile.mkdtemp(prefix="pdf_extract_")
        try:
            tmp_path = os.path.join(temp_dir, "input.pdf")
            with open(tmp_path, "wb") as f:
                f.write(pdf_bytes)

            page_data: list[tuple[str, dict[str, str]]] = []
            for page_num in range(page_count):
                page_images_dir = pathlib.Path(temp_dir) / f"images_p{page_num}"
                page_images_dir.mkdir(exist_ok=True)

                markdown_text = pymupdf4llm.to_markdown(
                    tmp_path,
                    pages=[page_num],
                    use_ocr=False,
                    write_images=True,
                    image_path=str(page_images_dir),
                    image_format="png",
                )

                if not markdown_text or not markdown_text.strip():
                    raise BadRequestError(
                        f"Unable to extract readable text from page {page_num + 1}"
                    )

                image_map: dict[str, str] = {}
                if page_images_dir.exists():
                    for img_file in page_images_dir.iterdir():
                        if img_file.is_file():
                            image_map[img_file.name] = str(img_file)

                page_data.append((markdown_text, image_map))

            return page_data, temp_dir
        except BaseException:
            if os.path.isdir(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            raise

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
    ) -> tuple[list[dict[str, Any]], int]:
        if not pdf_bytes:
            raise BadRequestError("Uploaded PDF is empty")

        page_data, temp_dir = await asyncio.to_thread(self._split_pdf_to_pages, pdf_bytes)
        try:

            async def process_page(
                page_num: int, markdown_text: str, image_map: dict[str, str]
            ) -> tuple[list[dict[str, Any]], int]:
                payloads, token_used = await self.generate_task_payloads_from_text(
                    text=markdown_text,
                    template=template,
                    space_id=space_id,
                    category_id=category_id,
                    forced_submitter_type=forced_submitter_type,
                    user_id=user_id,
                    default_topic_ids=default_topic_ids,
                )

                for payload in payloads:
                    if payload.get("description") and image_map:
                        payload["description"] = await self._upload_and_replace_images(
                            markdown_text=payload["description"],
                            image_map=image_map,
                        )

                return payloads, token_used

            results = await asyncio.gather(
                *[process_page(i, md, im) for i, (md, im) in enumerate(page_data)],
                return_exceptions=True,
            )

            all_payloads: list[dict[str, Any]] = []
            total_tokens = 0
            failed_pages: list[int] = []
            for i, result in enumerate(results):
                if isinstance(result, BaseException):
                    failed_pages.append(i + 1)
                    logger.warning("PDF page %d LLM processing failed: %s", i + 1, result)
                else:
                    payloads, tokens = result
                    all_payloads.extend(payloads)
                    total_tokens += tokens

            if not all_payloads:
                raise BadRequestError(f"All {len(page_data)} page(s) failed to process")

            if failed_pages:
                logger.warning(
                    "PDF processing: %d/%d pages succeeded, failed pages: %s",
                    len(page_data) - len(failed_pages),
                    len(page_data),
                    failed_pages,
                )

            return all_payloads, total_tokens
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
        if not self._llm_client.is_configured:
            raise BadRequestError("LLM is not configured")

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(text=normalized_text, template=template)

        try:
            response = await self._llm_client.get_completion(
                prompt=user_prompt,
                system_prompt=system_prompt,
                model_type="reasoning",
                json_response=True,
                timeout=self._timeout_seconds,
            )
        except LLMTimeoutError as exc:
            raise BadRequestError(str(exc)) from exc
        except LLMConnectionError as exc:
            raise BadRequestError(str(exc)) from exc
        except LLMAPIError as exc:
            raise BadRequestError(str(exc)) from exc

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

        return payloads, response.total_tokens

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
    ) -> str:
        """Upload extracted images to storage and replace local references with URLs.

        Returns the markdown text with image paths replaced by uploaded URLs.
        """
        if not image_map:
            return markdown_text

        storage = get_storage_backend()
        replaced = markdown_text

        for filename, local_path in image_map.items():
            if not await asyncio.to_thread(os.path.isfile, local_path):
                continue

            storage_key = generate_storage_key(filename, prefix="task-images")
            content = await asyncio.to_thread(pathlib.Path(local_path).read_bytes)

            import io as _io

            storage_url = await storage.upload(_io.BytesIO(content), storage_key, "image/png")

            # Convert relative storage URL to absolute URL pointing to the backend
            # storage_url is like "/uploads/task-images/2026/05/01/abc.png"
            # We prepend the backend base URL so the frontend can load images
            backend_base = settings.avatar_base_url.rstrip("/")
            url = f"{backend_base}{storage_url}"

            # Replace image references in markdown:
            #   ![alt](images/filename)  or  ![](images/filename)
            escaped_name = re.escape(filename)
            pattern = r"!\[([^\]]*)\]\([^)]*" + escaped_name + r"\)"
            replacement = r"![\1](" + url + ")"
            replaced = re.sub(pattern, replacement, replaced)

        return replaced

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
            "**输出格式要求**：直接输出一个 JSON 对象，包含 name、intro、description 三个字段。"
            "形如 "
            '{"name": "...", "intro": "...", "description": "..."}。\n\n'
            "**重要：只输出纯 JSON，不要用 ```json 代码块包裹，不要加任何前缀或后缀说明。**"
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
            raise BadRequestError(f"LLM response JSON must be an object, got {type(obj).__name__}.")
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
        name = str(llm_result.get("name") or template_defaults.get("name") or "").strip()
        intro = str(llm_result.get("intro") or template_defaults.get("intro") or "").strip()
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
