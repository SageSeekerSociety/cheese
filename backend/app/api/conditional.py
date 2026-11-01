"""条件请求的两块共用件：`If-None-Match` 的解析，和按响应体算 ETag。

抽出来是因为条件请求在这个平台上不止一处：头像那条路按文件字节算 tag，管理员的
名单按 data 算 tag，而**「怎么读 `If-None-Match`」只有一个答案**。各写一份的表现
是「头像那边认 `W/` 前缀、名单这边不认」，换一个浏览器或代理就有一边永远回 200
—— 这种毛病两边各自看都对，只能靠共用一份来根治。

放在 `app/api/`（不属于任何领域）：它不认识头像也不认识管理员，只认识 HTTP。
"""

import asyncio
import functools
import hashlib
import json
from typing import Any

from starlette.responses import Response

#: 清单类响应共用的缓存指令：它是**登录用户**的私有视图（每一行都带「与我的相关性」
#: 「是不是等我」这类按人算的字段），所以只能是 `private`；`no-cache` 要求客户端每次
#: 带 `If-None-Match` 回来问一句，命中 ETag 就回 304、空 body —— 没有变化的那些轮询
#: 和页面切换不再把几百 KB 到几 MB 的清单重传一遍。和 `admin_members` 那份名单同一个
#: 形状。
#:
#: 三份清单共用：项目的话题清单（侧栏每 30 秒轮询）、项目级的任务清单、房间级的任务
#: 清单（后两份是每次画侧栏的 rail 就要的）。
LIST_CACHE_CONTROL = "private, no-cache"


def if_none_match_hits(header: str, etag: str) -> bool:
    """Does ``If-None-Match`` say the client already has this exact representation?

    ``*`` means "any representation" and is what a client sends for a resource it
    has never seen. The header is a comma-separated list and each entry may carry
    the weak prefix ``W/``; both are handled because both are what browsers and
    proxies actually send.

    ``etag`` is the **unquoted** digest (the shape ``hashlib`` returns); the
    client's side is stripped of its own quotes before the comparison, which is
    what lets a strong tag from us match the (often weak) tag a proxy stored.

    原样搬自 `routes/avatars.py`（那边改成 import 这里，行为一字未动）：抽的是
    这一段，改的是它有几份。
    """
    if header.strip() == "*":
        return True
    for candidate in header.split(","):
        value = candidate.strip()
        if value.startswith("W/"):
            value = value[2:].lstrip()
        if value.strip('"') == etag:
            return True
    return False


def etag_for_json(data: Any) -> str:
    """A stable ETag for a response body, derived from the body alone.

    ``sort_keys`` plus the compact separators make the digest depend on the
    **content** of ``data`` and not on dict insertion order or whitespace — two
    requests that build the same roster must hash the same, or every poll would
    look like a change and the 304 would never fire. ``ensure_ascii=False`` keeps
    non-ASCII (handles, nicknames) hashing as their UTF-8 bytes, which is what a
    client comparing bytes would see.

    Returns the **unquoted** hex digest; the caller adds the quotes for the
    header — the same split as ``if_none_match_hits``, which compares unquoted.
    """
    canonical = _dumps(data)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


#: A list longer than this is written one element at a time, with a yield to the
#: event loop every ``YIELD_EVERY_ITEMS`` elements; anything shorter, and every
#: element, goes through the C encoder in one call.
SPLIT_LISTS_LONGER_THAN = 200
YIELD_EVERY_ITEMS = 500

_dumps = functools.partial(
    json.dumps, sort_keys=True, ensure_ascii=False, separators=(",", ":")
)


class _Body:
    """The body as it is written: UTF-8 pieces, their running digest, and how
    many list elements went out since the last yield."""

    def __init__(self) -> None:
        self.digest = hashlib.sha256()
        self.pieces: list[bytes] = []
        self.batch: list[str] = []
        self.since_yield = 0

    def write(self, text: str) -> None:
        self.batch.append(text)

    def flush(self) -> None:
        piece = "".join(self.batch).encode("utf-8")
        self.batch.clear()
        self.digest.update(piece)
        self.pieces.append(piece)


async def _write(value: Any, body: _Body) -> None:
    """``value`` as ``_dumps`` would write it, with the long lists split.

    A dict with string keys is walked in key order and a long list element by
    element, so the text is the same as one ``_dumps(value)``: the separators
    are ``,`` and ``:`` either way, and a part encoded alone is encoded exactly
    as it would be inside the whole. Any other dict (keys json.dumps would
    convert) and every short list or scalar is one C ``_dumps`` call.
    """
    if isinstance(value, dict) and all(type(key) is str for key in value):
        body.write("{")
        for index, key in enumerate(sorted(value)):
            body.write(("," if index else "") + _dumps(key) + ":")
            await _write(value[key], body)
        body.write("}")
    elif isinstance(value, list | tuple) and len(value) > SPLIT_LISTS_LONGER_THAN:
        body.write("[")
        for index, item in enumerate(value):
            body.write(("," if index else "") + _dumps(item))
            body.since_yield += 1
            if body.since_yield >= YIELD_EVERY_ITEMS:
                body.since_yield = 0
                body.flush()
                await asyncio.sleep(0)
        body.write("]")
    else:
        body.write(_dumps(value))


async def _encode_yielding(data: Any) -> tuple[bytes, str]:
    """The canonical body and its digest, yielding to the loop every
    ``YIELD_EVERY_ITEMS`` elements of a long list.

    The body is ``json.dumps(data, sort_keys=True, ensure_ascii=False,
    separators=(",", ":"))`` as UTF-8, byte for byte. One ``json.dumps`` of a
    60,000-row envelope held the loop for about 227 ms (#1825, 2026-10-17), so a
    long list is written element by element with yields between stretches.

    Each element still goes through the C encoder. The pure-Python
    ``iterencode`` this used before kept the loop free too, but cost about 47 ms
    of a 18-channel `GET /topics` on every poll (2026-11-01), several times the
    C encode, for lists far too short to need splitting.
    """
    body = _Body()
    await _write(data, body)
    body.flush()
    return b"".join(body.pieces), body.digest.hexdigest()


async def conditional_json(
    data: Any, if_none_match: str | None, *, cache_control: str = LIST_CACHE_CONTROL
) -> Response:
    """``data`` as a JSON response with its ETag, or a 304 when the client has it.

    The body is serialized once and the tag is the digest of those very bytes.
    Hashing a canonical copy and then letting the framework encode the payload
    again serialized a list of 1,300 tasks three times per read on dev
    (2026-10-08): about 200 of the 700 ms it took. The serialization is the
    canonical one ``etag_for_json`` describes, so equal content gives an equal
    tag however the payload was built.

    The encode yields to the event loop as it goes (``_encode_yielding``): a 304
    still needs the whole body to know its tag, so a large list used to stall
    every other request for the full encode even when nothing was sent back.
    """
    body, etag = await _encode_yielding(data)
    headers = {"Cache-Control": cache_control, "ETag": f'"{etag}"'}
    if if_none_match and if_none_match_hits(if_none_match, etag):
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json", headers=headers)
