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

import hashlib
from collections import OrderedDict

import httpx

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
        raise OfficeRenderFailed(f"这个格式不能转换为预览：{suffix or path}")
    if not endpoint:
        raise OfficeRenderUnavailable("这个部署没有启用文档预览")

    key = hashlib.sha256(raw).hexdigest()
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
