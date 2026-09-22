"""Word, PowerPoint and old spreadsheets, turned into something a browser can show.

A room that writes a report or a deck produces a file the browser cannot render.
Converting it once puts it on screen — for a document, to PDF, because PDF is the
one document format every browser already draws and it draws it with a selectable
text layer, so a reader can still point at a sentence and say what is wrong with
it; for a sheet, to the modern workbook format, because the reader that draws
sheets reads that and nothing older.

The conversion itself is LibreOffice, which lives in its own container
(``deploy/office-render``) for reasons of size and of the writable profile
directory it insists on. This module is the thin half: call it, cache what comes
back, and say plainly when it is not there.

Spreadsheets are deliberately never turned into PDF. A sheet converted to PDF
loses the thing that makes it a sheet — columns break across pages and a cell
stops having an address — so the browser renders those from the original bytes
instead. The one exception is the format no browser reader can open at all:
`.xls` becomes `.xlsx` and stays a sheet.

What may be converted is stated once, in ``PREVIEW_TARGETS``: a suffix in it can
be shown, and the value is the form to show it in. Everything else is handed over
as it is, because the browser draws it itself.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
from collections import OrderedDict
from dataclasses import dataclass

import httpx

#: What each suffix becomes on screen. A suffix absent from this table is not
#: converted at all — the bytes stand as they are, which is the right answer for
#: every format a browser already draws (a modern workbook, a CSV, an image, a
#: Markdown file). So this table is also the answer to "can this be shown by
#: converting it", and it is the only place that answer lives.
PREVIEW_TARGETS: dict[str, str] = {
    ".docx": ".pdf",
    ".doc": ".pdf",
    ".odt": ".pdf",
    ".rtf": ".pdf",
    ".pptx": ".pdf",
    ".ppt": ".pdf",
    ".odp": ".pdf",
    ".xls": ".xlsx",
}

#: The ones that become a PDF. Kept as its own name because the room-file route
#: asks exactly this question, and a caller that can only display pages should not
#: have to read the whole table to find out.
RENDERABLE_SUFFIXES: tuple[str, ...] = tuple(
    suffix for suffix, target in PREVIEW_TARGETS.items() if target == ".pdf"
)

#: What the render service answers with, and what it must have answered with
#: before the bytes are believed. A conversion that exits 0 having written
#: something else is the failure mode worth catching: a truncated or mislabelled
#: file looks exactly like a good one until a person opens it.
_TARGET_GUARDS: dict[str, tuple[str, bytes]] = {
    ".pdf": ("application/pdf", b"%PDF"),
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        b"PK\x03\x04",
    ),
}

#: One bound for what may be handed to the renderer, stated where the renderer is
#: called from rather than at each call site. It is the service's own ceiling
#: (``OFFICE_RENDER_MAX_BYTES``) restated here so that "too big to preview" is a
#: sentence a person reads, not a 413 nobody maps.
MAX_PREVIEW_BYTES = 10 * 1024 * 1024

#: One conversion measured 1.1s, and a preview panel re-reads on a timer, so the
#: same document would be converted again every few seconds without this. Keyed
#: by the bytes themselves *and* by what they are converted to: an agent that
#: rewrites the file gets a new key and therefore a new render, with no
#: invalidation to get wrong, and two different targets for the same bytes cannot
#: answer each other's request.
#:
#: Bounded by entry count and by total bytes — a dozen large decks would
#: otherwise be held forever in a process that is also serving requests.
_CACHE_MAX_ENTRIES = 32
_CACHE_MAX_BYTES = 128 * 1024 * 1024
_cache: OrderedDict[str, bytes] = OrderedDict()
_cache_bytes = 0

#: The conversions running right now, by cache key. A document is previewed by
#: whoever opens the page, and two people opening the same item at the same
#: moment is the ordinary case rather than a rare one — without this both miss the
#: cache and both ask the renderer, which is 1.1 seconds of LibreOffice apiece for
#: one answer.
#:
#: Loop-affine: every mutation happens inside ``convert``, which is awaited on the
#: event loop and never handed to a thread.
_inflight: dict[str, asyncio.Future[bytes]] = {}


class OfficeRenderUnavailable(RuntimeError):
    """The deployment has no renderer, or the renderer did not answer."""


class OfficeRenderFailed(RuntimeError):
    """The renderer answered, and could not convert this document."""


@dataclass(frozen=True)
class Preview:
    """What a browser is given for one file, and what it is."""

    data: bytes
    media_type: str


def suffix_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def is_renderable(path: str) -> bool:
    return suffix_of(path) in RENDERABLE_SUFFIXES


def is_convertible(path: str) -> bool:
    """Whether showing this file at all means converting it first."""
    return suffix_of(path) in PREVIEW_TARGETS


def media_type_of(path: str) -> str:
    """What the bytes are, for a caller that has not converted anything.

    Nothing in this product reads this header — the viewers are handed bytes and
    choose a reader from the suffix. It is set to the truth anyway: a response
    labelled ``application/octet-stream`` is a lie the next person has to check.
    """
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def _remember(key: str, converted: bytes) -> None:
    global _cache_bytes
    if len(converted) > _CACHE_MAX_BYTES:
        return
    _cache[key] = converted
    _cache_bytes += len(converted)
    while _cache and (
        len(_cache) > _CACHE_MAX_ENTRIES or _cache_bytes > _CACHE_MAX_BYTES
    ):
        _, evicted = _cache.popitem(last=False)
        _cache_bytes -= len(evicted)


def _key(target: str, raw: bytes) -> str:
    return f"{target}:{hashlib.sha256(raw).hexdigest()}"


async def _ask(
    raw: bytes, suffix: str, target: str, endpoint: str, timeout: float
) -> bytes:
    """One request to the render service, and the two ways it can decline."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if target == ".pdf":
                response = await client.post(
                    endpoint.rstrip("/") + "/render",
                    params={"suffix": suffix},
                    content=raw,
                    headers={"Content-Type": "application/octet-stream"},
                )
            else:
                response = await client.post(
                    endpoint.rstrip("/") + "/convert",
                    params={"suffix": suffix, "to": target.lstrip(".")},
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

    magic = _TARGET_GUARDS[target][1]
    if not response.content.startswith(magic):
        raise OfficeRenderFailed("转换结果不是有效的文件")
    return response.content


async def convert(
    raw: bytes, path: str, endpoint: str | None, timeout: float = 90.0
) -> bytes:
    """`raw` in the form `path`'s suffix is previewed as, converting if need be.

    Raises ``OfficeRenderFailed`` for a suffix this module does not convert, so a
    caller that only ever wants a PDF cannot get a workbook back by accident.
    """
    suffix = suffix_of(path)
    target = PREVIEW_TARGETS.get(suffix)
    if target is None:
        raise OfficeRenderFailed(f"这个格式不能转换为预览：{suffix or path}")
    if not endpoint:
        raise OfficeRenderUnavailable("这个部署没有启用文档预览")

    key = _key(target, raw)
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        return cached

    running = _inflight.get(key)
    if running is not None:
        return await asyncio.shield(running)

    mine: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
    # A future nobody awaits still has to be read, or asyncio reports the
    # exception as never retrieved when it is collected — a warning about this
    # module's correctness printed at a moment nobody can connect to a request.
    mine.add_done_callback(lambda done: done.cancelled() or done.exception())
    _inflight[key] = mine
    try:
        converted = await _ask(raw, suffix, target, endpoint, timeout)
    except BaseException as exc:
        # Whatever went wrong reaches everyone who was waiting on this key, and
        # reaches them as itself: a follower that is told "cannot convert this
        # file" has to be able to say which file and why.
        _inflight.pop(key, None)
        mine.set_exception(exc)
        raise
    _inflight.pop(key, None)
    _remember(key, converted)
    mine.set_result(converted)
    return converted


async def render_to_pdf(
    raw: bytes, path: str, endpoint: str | None, timeout: float = 90.0
) -> bytes:
    """The PDF for `raw`, converting through the render service if need be."""
    return await convert(raw, path, endpoint, timeout)


async def preview(raw: bytes, path: str, endpoint: str | None) -> Preview:
    """Whatever a browser should be given for this file.

    A file this module can convert is converted; every other file is handed over
    as it is, because the browser draws it itself. So a caller that wants to
    *show* something never has to know which case it is in — and a caller that
    cannot convert anything yet still gets the bytes.
    """
    if not is_convertible(path):
        return Preview(data=raw, media_type=media_type_of(path))
    if len(raw) > MAX_PREVIEW_BYTES:
        raise OfficeRenderFailed(
            f"文件超过 {MAX_PREVIEW_BYTES // (1024 * 1024)}MB，无法生成预览"
        )
    converted = await convert(raw, path, endpoint)
    return Preview(
        data=converted,
        media_type=_TARGET_GUARDS[PREVIEW_TARGETS[suffix_of(path)]][0],
    )
