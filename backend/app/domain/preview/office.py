"""Word and PowerPoint deliverables, turned into something a browser can show.

A room that writes a report or a deck produces a file the browser cannot render.
Converting it to PDF once puts it on screen, because PDF is the one document
format every browser already draws — and it draws it with a selectable text
layer, so a reader can still point at a sentence and say what is wrong with it.

The conversion itself is LibreOffice, which lives in its own container
(``deploy/office-render``) for reasons of size and of the writable profile
directory it insists on. This module is the thin half: call it, cache what comes
back, and say plainly when it is not there.

Spreadsheets are largely absent, for a reason of their own: a sheet converted to
PDF loses the thing that makes it a sheet — columns break across pages and a cell
stops having an address — so those are drawn from the original bytes instead.

One of them cannot be. `.xls` predates the zip container that OOXML is, so
nothing in a room can read a cell out of it; it converts. But it converts to
`.xlsx` rather than to PDF: the reader keeps a cell address either way, and an
address is the whole reason a sheet is worth looking at.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict

import httpx

from app.domain.documents.convert import (
    CONVERTIBLE,
    ConvertFailed,
    ConvertUnavailable,
    convert,
)

#: What this module will send onward. A suffix outside this set never reaches the
#: service, so an unsupported file fails here with a sentence rather than there
#: with an HTTP code.
RENDERABLE_SUFFIXES = (".docx", ".doc", ".odt", ".rtf", ".pptx", ".ppt", ".odp")

#: One conversion measured 1.1s, and a preview panel re-reads on a timer, so the
#: same document would be converted again every few seconds without this. Keyed
#: by the bytes themselves: an agent that rewrites the file gets a new key and
#: therefore a new render, with no invalidation to get wrong. The target is part
#: of the key because the same bytes can be asked for two ways — a file named
#: `.xls` and the same file named something else must not answer for each other.
#:
#: Bounded by entry count and by total bytes — a dozen large decks would
#: otherwise be held forever in a process that is also serving requests.
_CACHE_MAX_ENTRIES = 32
_CACHE_MAX_BYTES = 128 * 1024 * 1024
_cache: OrderedDict[str, bytes] = OrderedDict()
_cache_bytes = 0


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


def is_projectable(path: str) -> bool:
    """This file's cells can be had, but only by converting it first.

    Asked of `CONVERTIBLE` rather than a second list of its own: which formats
    the service will turn into a workbook is a fact about the service, and a
    copy here would be the copy that goes stale.
    """
    return "xlsx" in CONVERTIBLE.get(suffix_of(path), ())


def _key(raw: bytes, target: str) -> str:
    return f"{target}:{hashlib.sha256(raw).hexdigest()}"


def _remember(key: str, content: bytes) -> None:
    global _cache_bytes
    if len(content) > _CACHE_MAX_BYTES:
        return
    _cache[key] = content
    _cache_bytes += len(content)
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
        raise OfficeRenderFailed(f"这个格式不能转换为预览：{suffix or path}")
    if not endpoint:
        raise OfficeRenderUnavailable("这个部署没有启用文档预览")

    key = _key(raw, "pdf")
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        return cached

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                endpoint.rstrip("/") + "/render",
                params={"suffix": suffix},
                content=raw,
                headers={"Content-Type": "application/octet-stream"},
            )
    except Exception as exc:  # noqa: BLE001 — every transport failure reads alike
        raise OfficeRenderUnavailable("文档预览服务暂时无法访问") from exc

    if response.status_code != 200:
        # The service states its own refusals in a sentence; pass that through
        # rather than an HTTP code the reader cannot act on.
        detail = ""
        try:
            detail = str(response.json().get("error") or "")
        except Exception:  # noqa: BLE001 — a non-JSON body is just no detail
            detail = ""
        if response.status_code >= 500:
            raise OfficeRenderUnavailable(detail or "文档预览服务出错")
        raise OfficeRenderFailed(
            detail or f"无法转换这个文件（HTTP {response.status_code}）"
        )

    pdf = response.content
    if not pdf.startswith(b"%PDF"):
        raise OfficeRenderFailed("转换结果不是有效的 PDF")
    _remember(key, pdf)
    return pdf


async def project_to_xlsx(
    raw: bytes, path: str, endpoint: str | None, timeout: float = 120.0
) -> bytes:
    """`raw` as a workbook the sheet viewer can read — the `.xls` case.

    A projection, not an upgrade: nothing is written back, so the file the room
    keeps is still the one the user handed over. `cheese convert` is the other
    thing, and it produces a new file beside the original on purpose.

    The failure split is `render_to_pdf`'s, because it means the same things:
    no renderer is the deployment's state (503), a document the service refuses
    is this file's (400).
    """
    if not is_projectable(path):
        raise OfficeRenderFailed(f"这个格式不能转换为表格：{suffix_of(path) or path}")

    key = _key(raw, "xlsx")
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        return cached

    try:
        made = await convert(raw, path, "xlsx", endpoint, timeout=timeout)
    except ConvertUnavailable as exc:
        raise OfficeRenderUnavailable(str(exc)) from exc
    except ConvertFailed as exc:
        raise OfficeRenderFailed(str(exc)) from exc

    _remember(key, made)
    return made
