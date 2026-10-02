"""Conditional reads of the GitHub resources the platform reads over and over.

The pollers and the draft-PR sweep read the same branches, compares, PRs and
check runs on every pass, and most of them have not changed since the last
one. Every non-streamed GET a `forge_client` sends goes through `send` here.
GitHub answers a read that sends `If-None-Match` with the ETag of the
representation it still holds with 304 Not Modified, and a 304 is not charged
to the installation's hourly quota. So the last answer to each such read is
kept here, with its ETag, and a 304 hands that answer back.

Keyed by installation, URL and `Accept`, not by token. Installation tokens are
re-minted every hour, and a key that included the token would start every hour
cold. This is safe whatever GitHub decides about a token it has not seen with
that ETag: a 304 is GitHub confirming that the representation is the one
cached, and anything else is a full answer that replaces it.

Only requests carrying a token the platform minted for an installation take
part (`forge_quota.installation_of`); Forgejo and anything else go straight
through, as does any answer that carries no ETag.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.core import forge_quota

#: How many answers are kept, least recently used first out.
MAX_ENTRIES = 1024
#: An answer larger than this is not kept; the polled reads are a few KiB.
MAX_BODY_BYTES = 256 * 1024


@dataclass(frozen=True)
class _Kept:
    etag: str
    headers: list[tuple[str, str]]
    body: bytes


_kept: OrderedDict[tuple[int, str, str], _Kept] = OrderedDict()


async def send(
    send: Callable[..., Awaitable[httpx.Response]],
    request: httpx.Request,
    **kwargs: Any,
) -> httpx.Response:
    """Send one non-streamed GET, answered from the last answer when GitHub
    says that answer still holds."""
    installation = forge_quota.installation_of(request)
    key = (
        None
        if installation is None
        else (installation, str(request.url), request.headers.get("accept", ""))
    )
    kept = _kept.get(key) if key is not None else None
    if kept is not None:
        request.headers["If-None-Match"] = kept.etag
    response = await send(request, **kwargs)
    if key is None:
        return response
    if response.status_code == 304 and kept is not None:
        _kept.move_to_end(key)
        return httpx.Response(
            200, headers=kept.headers, content=kept.body, request=request
        )
    etag = response.headers.get("etag")
    if response.status_code == 200 and etag and len(response.content) <= MAX_BODY_BYTES:
        _kept[key] = _Kept(
            etag,
            [
                (name, value)
                for name, value in response.headers.items()
                if name.lower() not in ("content-encoding", "content-length")
            ],
            response.content,
        )
        _kept.move_to_end(key)
        while len(_kept) > MAX_ENTRIES:
            _kept.popitem(last=False)
    elif response.status_code in (200, 404):
        # The resource changed shape or went away; a refusal says nothing
        # about it and keeps what was there.
        _kept.pop(key, None)
    return response
