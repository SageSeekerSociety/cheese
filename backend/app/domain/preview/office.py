"""Word and PowerPoint deliverables, turned into something a browser can show.

A room that writes a report or a deck produces a file the browser cannot render.
Converting it to PDF once puts it on screen, because PDF is the one document
format every browser already draws — and it draws it with a selectable text
layer, so a reader can still point at a sentence and say what is wrong with it.

The conversion itself is LibreOffice, which lives in its own container
(``deploy/office-render``) for reasons of size and of the writable profile
directory it insists on. This module is the thin half: call it, cache what comes
back, and say plainly when it is not there.

Spreadsheets are deliberately absent. A sheet converted to PDF loses the thing
that makes it a sheet — columns break across pages and a cell stops having an
address — so the browser renders those from the original bytes instead.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
from collections import OrderedDict
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.errors import SystemBusyError, ValidationError
from app.domain.block.notice_text import exception_text, say

logger = logging.getLogger(__name__)

#: What this module will send onward. A suffix outside this set never reaches the
#: service, so an unsupported file fails here with a sentence rather than there
#: with an HTTP code.
RENDERABLE_SUFFIXES = (".docx", ".doc", ".odt", ".rtf", ".pptx", ".ppt", ".odp")

#: One conversion measured 1.1s, and a preview panel re-reads on a timer, so the
#: same document would be converted again every few seconds without this. Keyed
#: by the bytes themselves: an agent that rewrites the file gets a new key and
#: therefore a new render, with no invalidation to get wrong.
#:
#: Bounded by entry count and by total bytes — a dozen large decks would
#: otherwise be held forever in a process that is also serving requests.
_CACHE_MAX_ENTRIES = 32
_CACHE_MAX_BYTES = 128 * 1024 * 1024
_cache: OrderedDict[str, bytes] = OrderedDict()
_cache_bytes = 0


#: Converted PDFs on disk, by the same key. The memory cache dies with every
#: deploy, and on dev that was often enough that the first open of a Word file
#: after one waited 3–3.6s for LibreOffice (measured 2026-09-27). The disk copy
#: survives; oldest files go first past the cap.
_DISK_MAX_BYTES = 1024 * 1024 * 1024


def _disk_root() -> Path:
    return Path(settings.workspace_root) / ".preview-cache"


def _disk_get(key: str) -> bytes | None:
    path = _disk_root() / f"{key}.pdf"
    try:
        data = path.read_bytes()
    except OSError:
        return None
    try:
        os.utime(path)  # recently used: pruned last
    except OSError:
        pass
    return data if data.startswith(b"%PDF") else None


def _disk_put(key: str, pdf: bytes) -> None:
    root = _disk_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        staging = root / f".{key}.{uuid.uuid4().hex}"
        staging.write_bytes(pdf)
        staging.replace(root / f"{key}.pdf")
        files = sorted(root.glob("*.pdf"), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        while files and total > _DISK_MAX_BYTES:
            oldest = files.pop(0)
            total -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)
    except OSError:
        # A full or read-only disk costs speed, never the preview itself.
        logger.warning("preview cache write failed", exc_info=True)


class OfficeRenderUnavailable(RuntimeError):
    """The deployment has no renderer, or the renderer did not answer."""


class OfficeRenderFailed(RuntimeError):
    """The renderer answered, and could not convert this document."""


def suffix_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def is_renderable(path: str) -> bool:
    return suffix_of(path) in RENDERABLE_SUFFIXES


def _remember(key: str, pdf: bytes) -> None:
    global _cache_bytes
    if len(pdf) > _CACHE_MAX_BYTES:
        return
    _cache[key] = pdf
    _cache_bytes += len(pdf)
    while _cache and (
        len(_cache) > _CACHE_MAX_ENTRIES or _cache_bytes > _CACHE_MAX_BYTES
    ):
        _, evicted = _cache.popitem(last=False)
        _cache_bytes -= len(evicted)


async def render_to_pdf(
    raw: bytes, path: str, endpoint: str | None, timeout: float = 90.0
) -> bytes:
    """The PDF for `raw`, converting through the render service if need be."""
    suffix = suffix_of(path)
    if suffix not in RENDERABLE_SUFFIXES:
        raise OfficeRenderFailed(
            say("previewFormatUnsupportedNamed", format=suffix or path)
        )
    if not endpoint:
        raise OfficeRenderUnavailable(say("previewDisabled"))

    key = hashlib.sha256(raw).hexdigest()
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        return cached
    stored = await asyncio.to_thread(_disk_get, key)
    if stored is not None:
        _remember(key, stored)
        return stored

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                endpoint.rstrip("/") + "/render",
                params={"suffix": suffix},
                content=raw,
                headers={"Content-Type": "application/octet-stream"},
            )
    except Exception as exc:  # noqa: BLE001 — every transport failure reads alike
        raise OfficeRenderUnavailable(say("previewServiceUnreachable")) from exc

    if response.status_code != 200:
        # The service states its own refusals in a sentence; pass that through
        # rather than an HTTP code the reader cannot act on.
        detail = ""
        try:
            detail = str(response.json().get("error") or "")
        except Exception:  # noqa: BLE001 — a non-JSON body is just no detail
            detail = ""
        if response.status_code >= 500:
            raise OfficeRenderUnavailable(detail or say("previewServiceError"))
        raise OfficeRenderFailed(
            detail or say("convertFailedStatus", status=response.status_code)
        )

    pdf = response.content
    if not pdf.startswith(b"%PDF"):
        raise OfficeRenderFailed(say("previewNotPdf"))
    _remember(key, pdf)
    await asyncio.to_thread(_disk_put, key, pdf)
    return pdf


_warming: set[asyncio.Task] = set()


def prewarm(raw: bytes, path: str) -> None:
    """Convert a just-saved Word or PowerPoint file now, so opening it is a hit.

    Fire and forget: a save must not wait for LibreOffice, and a conversion that
    fails here fails again, with its sentence, when someone opens the preview.
    """
    endpoint = settings.office_render_endpoint
    if not endpoint or not is_renderable(path):
        return

    async def run() -> None:
        try:
            await render_to_pdf(raw, path, endpoint)
        except Exception:  # noqa: BLE001 — see docstring
            logger.info("preview prewarm skipped for %s", path, exc_info=True)

    try:
        task = asyncio.get_running_loop().create_task(run())
    except RuntimeError:
        return
    _warming.add(task)
    task.add_done_callback(_warming.discard)


async def preview_pdf(data: bytes, filename: str) -> bytes:
    """一份 Office 文档转成 PDF，给页面预览：交付的某一版、资料库里的一份。"""
    if len(data) > 10 * 1024 * 1024:
        raise ValidationError(say("previewOver10Mb"))
    if not is_renderable(filename):
        raise ValidationError(say("previewFormatUnsupported"))
    try:
        return await render_to_pdf(data, filename, settings.office_render_endpoint)
    except OfficeRenderUnavailable as exc:
        raise SystemBusyError(exception_text(exc)) from exc
    except OfficeRenderFailed as exc:
        raise ValidationError(exception_text(exc)) from exc
