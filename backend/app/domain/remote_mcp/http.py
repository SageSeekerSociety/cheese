"""The one HTTP client every upstream call of a remote MCP server goes through.

Every URL here comes from somewhere a project member controls — the committed
`.mcp.json`, and the metadata documents the server it names returns — so each
request is refused before it is sent when its host is this platform's own
network. Redirects are not followed for the same reason.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from app.core.config import settings
from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.integration.service import refuse_internal_host

#: Some MCP hosts sit behind bot filters that refuse the default Python agent.
USER_AGENT = "Cheese-MCP/1"
_GUARD_S = 300
_checked: dict[str, float] = {}


async def _guard(request: httpx.Request) -> None:
    if settings.remote_mcp_allow_private_hosts:
        return
    if request.url.scheme != "https":
        raise ValidationError(say("mcpServerNeedsHttps", host=request.url.host))
    host = request.url.host
    now = time.monotonic()
    if now - _checked.get(host, -_GUARD_S) < _GUARD_S:
        return
    await asyncio.to_thread(refuse_internal_host, host, say("nounMcpServer"))
    _checked[host] = now


def client(*, timeout: float | None = 30) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=15),
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
        event_hooks={"request": [_guard]},
    )
