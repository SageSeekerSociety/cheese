"""Turning one document format into another, where a room cannot.

Two needs, one mechanism.

The pre-2007 binary formats — `.doc`, `.ppt`, `.xls` — are not zips, and nothing
a room has can read or write them. The honest alternatives are to convert them
or to tell the user to open Office and do it himself; #1086 chose converting,
and to say plainly that it happened, because the converted file and the original
are not the same document and which one he forwards is his decision.

The other need is the room's own eyes. A delivery whose layout is wrong fails
silently — the text extracts correctly and the file opens — so the room has to
look at a page. Converting to PDF here and rasterising there is that look.

Both are LibreOffice, so both are ``deploy/office-render``. This module is the
thin half.
"""

from __future__ import annotations

import httpx

#: Which conversions the service will do, mirrored here so a request that cannot
#: be meant is refused with a sentence instead of an HTTP code from one hop
#: further away. A format converting to itself is absent on purpose: that is
#: recalculation, and it lives in `spreadsheet.py` because it needs more than a
#: filter change.
CONVERTIBLE: dict[str, tuple[str, ...]] = {
    ".doc": ("docx", "pdf"),
    ".rtf": ("docx", "pdf"),
    ".odt": ("docx", "pdf"),
    ".ppt": ("pptx", "pdf"),
    ".odp": ("pptx", "pdf"),
    ".xls": ("xlsx",),
    ".ods": ("xlsx",),
    ".docx": ("pdf",),
    ".pptx": ("pdf",),
    ".xlsx": ("pdf",),
}

#: The formats whose only way into a room is a conversion. Named separately
#: because the sentence a user gets is different: for these the platform is not
#: offering a convenience, it is the only path.
LEGACY_SUFFIXES = (".doc", ".ppt", ".xls")


class ConvertUnavailable(RuntimeError):
    """The deployment has no converter, or the converter did not answer."""


class ConvertFailed(RuntimeError):
    """The converter answered, and could not convert this document."""


def suffix_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def upgraded_name(path: str, target: str) -> str:
    """`报告.doc` converted to docx is `报告.docx`, beside the original.

    The original is never written over: it is the user's file, and a conversion
    is a new document that happens to say the same thing.
    """
    suffix = suffix_of(path)
    stem = path[: -len(suffix)] if suffix else path
    return f"{stem}.{target}"


async def convert(
    raw: bytes, path: str, target: str, endpoint: str | None, timeout: float = 120.0
) -> bytes:
    """`raw` in `target`'s format, converted through the render service."""
    suffix = suffix_of(path)
    target = target.lower().strip().lstrip(".")
    allowed = CONVERTIBLE.get(suffix)
    if not allowed:
        raise ConvertFailed(f"不能转换这个格式：{suffix or path}")
    if target not in allowed:
        raise ConvertFailed(f"{suffix} 只能转成 {'、'.join(allowed)}，收到 {target!r}")
    if not endpoint:
        raise ConvertUnavailable("这个部署没有启用格式转换")

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                endpoint.rstrip("/") + "/convert",
                params={"suffix": suffix, "to": target},
                content=raw,
                headers={"Content-Type": "application/octet-stream"},
            )
    except Exception as exc:  # noqa: BLE001 — every transport failure reads alike
        raise ConvertUnavailable("格式转换服务暂时无法访问") from exc

    if response.status_code != 200:
        detail = ""
        try:
            detail = str(response.json().get("error") or "")
        except Exception:  # noqa: BLE001 — a non-JSON body is just no detail
            detail = ""
        if response.status_code >= 500 or response.status_code in (404, 405):
            # 404/405: the running renderer predates this endpoint. That is the
            # deployment's state, not a problem with the document.
            raise ConvertUnavailable(detail or "格式转换服务出错")
        raise ConvertFailed(
            detail or f"无法转换这个文件（HTTP {response.status_code}）"
        )

    made = response.content
    if not made:
        raise ConvertFailed("转换没有产出内容")
    # Every target here is either a zip (OOXML) or a PDF. Checking is what keeps
    # an error page from being written into the workspace under a real name.
    expected = b"%PDF" if target == "pdf" else b"PK"
    if not made.startswith(expected):
        raise ConvertFailed(f"转换结果不是一个 {target}")
    return made
