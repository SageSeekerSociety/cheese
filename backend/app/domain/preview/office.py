"""Office deliverables, turned into something a browser can show.

A room that writes a report or a deck produces a file the browser cannot render.
Converting it to PDF once puts it on screen, because PDF is the one document
format every browser already draws — and it draws it with a selectable text
layer, so a reader can still point at a sentence and say what is wrong with it.

The conversion itself is LibreOffice, which lives in its own container
(``deploy/office-render``) for reasons of size and of the writable profile
directory it insists on. This module is the thin half: call it, cache what comes
back, and say plainly when it is not there.

Spreadsheets are deliberately absent from that path. A sheet converted to PDF
loses the thing that makes it a sheet — columns break across pages and a cell
stops having an address — so the browser renders those from the original bytes
instead.

There is a second answer for the three formats OfficeCLI reads, ``render_to_html``:
one self-contained page, rendered by the same container from the same bytes. It
is not a replacement for the PDF, it is the answer to a different question. A
PDF is a picture of the document; the page keeps what the picture dissolved —
a chart's shape, the fill behind a header row, and above all each element's
address (``/body/p[7]``, ``/数据/B2``), which is the same string officecli's own
``set``/``add``/``remove`` take. That is what makes "改这一格" mean one cell to
the reader who said it and to whoever has to change it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.errors import SystemBusyError, ValidationError
from app.core.sentences import exception_text, say

logger = logging.getLogger(__name__)

#: What this module will send onward. A suffix outside this set never reaches the
#: service, so an unsupported file fails here with a sentence rather than there
#: with an HTTP code.
RENDERABLE_SUFFIXES = (".docx", ".doc", ".odt", ".rtf", ".pptx", ".ppt", ".odp")

#: What the web view covers. Not the same set: it is OfficeCLI that renders the
#: page, and OfficeCLI reads these three and no more — while ``.xlsx``, absent
#: from the PDF set, is here for exactly the reason it is absent there.
HTML_SUFFIXES = (".docx", ".xlsx", ".pptx")

#: One conversion measured 1.1s, and a preview panel re-reads on a timer, so the
#: same document would be converted again every few seconds without this. Keyed
#: by the bytes themselves: an agent that rewrites the file gets a new key and
#: therefore a new render, with no invalidation to get wrong.
#:
#: Both outputs share the one store and its one budget: the same bytes asked for
#: as a page and as a printout are two different answers (a workbook is both), so
#: the page's key carries its kind in front of the digest. The PDF's key is the
#: bare digest it has always been, which also keeps the copies already on disk
#: valid across this change.
#:
#: Bounded by entry count and by total bytes — a dozen large decks would
#: otherwise be held forever in a process that is also serving requests.
_CACHE_MAX_ENTRIES = 32
_CACHE_MAX_BYTES = 128 * 1024 * 1024
_cache: OrderedDict[str, bytes] = OrderedDict()
_cache_bytes = 0

#: What the page's cache key starts with: a page is the bigger of the two
#: outputs — a 2000-row sheet measured 3.0MB — and it is the one that fills the
#: budget above.
HTML_KEY_PREFIX = "html:"


#: Converted PDFs on disk, by the same key. The memory cache dies with every
#: deploy, and on dev that was often enough that the first open of a Word file
#: after one waited 3–3.6s for LibreOffice (measured 2026-09-27). The disk copy
#: survives; oldest files go first past the cap.
_DISK_MAX_BYTES = 1024 * 1024 * 1024


def _disk_root() -> Path:
    return Path(settings.workspace_root) / ".preview-cache"


def _looks_like_pdf(data: bytes) -> bool:
    return data.startswith(b"%PDF")


def _looks_like_html(data: bytes) -> bool:
    """A page at all, rather than some other kind of bytes.

    Weaker than the check on a PDF, and it has to be: a PDF opens with `%PDF`
    or it is not a PDF, while an HTML document may leave out its doctype and
    even its `<html>` tag. So this asks the one question that has an answer —
    is this a page — and not whether it is the right page.

    What it is worth is the case it was written for: something between here and
    the service answering 200 with a body that is not a page at all — an empty
    body, a JSON payload, a stray file — which would otherwise reach the frame
    as the reader's document.
    """
    return b"<html" in data[:2048].lower()


def _disk_get(key: str, ext: str, looks_right: Callable[[bytes], bool]) -> bytes | None:
    path = _disk_root() / f"{key}.{ext}"
    try:
        data = path.read_bytes()
    except OSError:
        return None
    try:
        os.utime(path)  # recently used: pruned last
    except OSError:
        pass
    return data if looks_right(data) else None


def _disk_put(key: str, ext: str, data: bytes) -> None:
    root = _disk_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        staging = root / f".{key}.{uuid.uuid4().hex}"
        staging.write_bytes(data)
        staging.replace(root / f"{key}.{ext}")
        # Both kinds share one budget. Named by extension rather than by a
        # wildcard: the half-written file goes in under a leading dot, and
        # ``glob('*')`` matches those too.
        files = sorted(
            (
                f
                for f in root.iterdir()
                if f.is_file() and f.suffix in (".pdf", ".html")
            ),
            key=lambda f: f.stat().st_mtime,
        )
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


def is_html_renderable(path: str) -> bool:
    return suffix_of(path) in HTML_SUFFIXES


def _remember(key: str, rendered: bytes) -> None:
    global _cache_bytes
    if len(rendered) > _CACHE_MAX_BYTES:
        return
    _cache[key] = rendered
    _cache_bytes += len(rendered)
    while _cache and (
        len(_cache) > _CACHE_MAX_ENTRIES or _cache_bytes > _CACHE_MAX_BYTES
    ):
        _, evicted = _cache.popitem(last=False)
        _cache_bytes -= len(evicted)


async def _lookup(
    key: str, ext: str, looks_right: Callable[[bytes], bool]
) -> bytes | None:
    """The memory copy, else the disk copy, else nothing."""
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        return cached
    stored = await asyncio.to_thread(_disk_get, key, ext, looks_right)
    if stored is not None:
        _remember(key, stored)
    return stored


async def _keep(key: str, ext: str, rendered: bytes) -> None:
    _remember(key, rendered)
    await asyncio.to_thread(_disk_put, key, ext, rendered)


async def _ask(
    endpoint: str, path: str, suffix: str, raw: bytes, timeout: float
) -> httpx.Response:
    """One call to the render service, with every transport failure read alike."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(
                endpoint.rstrip("/") + path,
                params={"suffix": suffix},
                content=raw,
                headers={"Content-Type": "application/octet-stream"},
            )
    except Exception as exc:  # noqa: BLE001 — see docstring
        raise OfficeRenderUnavailable(say("previewServiceUnreachable")) from exc


def _refusal(response: httpx.Response) -> None:
    """Turn a non-200 into the failure it is, or return on 200.

    The service states its own refusals in a sentence; pass that through rather
    than an HTTP code the reader cannot act on. Which side is at fault decides
    the sentence: 5xx is the deployment's (absent, overloaded, timed out) and
    4xx is this file's, and the two must not collapse into one — only the second
    is about something a retry will not fix.
    """
    if response.status_code == 200:
        return
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
    kept = await _lookup(key, "pdf", _looks_like_pdf)
    if kept is not None:
        return kept

    response = await _ask(endpoint, "/render", suffix, raw, timeout)
    _refusal(response)

    pdf = response.content
    if not _looks_like_pdf(pdf):
        raise OfficeRenderFailed(say("previewNotPdf"))
    await _keep(key, "pdf", pdf)
    return pdf


async def render_to_html(
    raw: bytes, path: str, endpoint: str | None, timeout: float = 90.0
) -> bytes:
    """The one HTML page for `raw`, converting through the render service.

    The page is served into a sandboxed frame and gets there whole: everything
    it draws is in the bytes (inline styles, inline SVG), so nothing here has to
    be fetched alongside it. What it does *not* get is the network — the pages
    officecli emits reach for a font CDN, a KaTeX CDN and a WebGL library on the
    vendor's own host, and a reader opening a room's internal document should not
    be made to call out to any of them. Formulas fall back to their source text
    and a missing typeface falls back to the reader's, which is what the page
    itself does when those fetches fail.
    """
    suffix = suffix_of(path)
    if suffix not in HTML_SUFFIXES:
        raise OfficeRenderFailed(
            say("previewFormatUnsupportedNamed", format=suffix or path)
        )
    if not endpoint:
        raise OfficeRenderUnavailable(say("previewDisabled"))

    key = HTML_KEY_PREFIX + hashlib.sha256(raw).hexdigest()
    kept = await _lookup(key, "html", _looks_like_html)
    if kept is not None:
        return kept

    response = await _ask(endpoint, "/html", suffix, raw, timeout)
    _refusal(response)

    page = response.content
    if not _looks_like_html(page):
        raise OfficeRenderFailed(say("previewNotHtml"))
    await _keep(key, "html", page)
    return page


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
