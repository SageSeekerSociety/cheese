"""条件请求的两块共用件：`If-None-Match` 的解析，和按响应体算 ETag。

抽出来是因为条件请求在这个平台上不止一处：头像那条路按文件字节算 tag，管理员的
名单按 data 算 tag，而**「怎么读 `If-None-Match`」只有一个答案**。各写一份的表现
是「头像那边认 `W/` 前缀、名单这边不认」，换一个浏览器或代理就有一边永远回 200
—— 这种毛病两边各自看都对，只能靠共用一份来根治。

放在 `app/api/`（不属于任何领域）：它不认识头像也不认识管理员，只认识 HTTP。
"""

import hashlib
import json
from typing import Any

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
    canonical = json.dumps(
        data, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
