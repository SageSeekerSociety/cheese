"""Access tokens of a closed account stop working at once.

An access token is verified by its signature alone (``app.common.auth``), so
one issued before the account was closed would otherwise keep working until
it expires. Closing an account writes when it closed here, for as long as any
token issued before then can live, and every request carrying a token for it
issued no later than that is refused before it reaches a route.

Redis is what every process shares. When it cannot be asked the request goes
through and the failure is logged: a token of a closed account can then live
out its last minutes, but it acts for an account whose memberships, devices
and sign-in methods are already gone.
"""

import time
from typing import Any

from starlette.datastructures import Headers, QueryParams
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.common.auth import verify_access_token
from app.core.config import settings
from app.core.errors import format_error_response
from app.core.obs import get_logger
from app.core.redis import get_redis_client

_log = get_logger("app.deleted_accounts")

#: A token issued the moment before the account closed lives this long.
_GRACE_S = 60


def _key(user_id: int) -> str:
    return f"account-closed:{user_id}"


async def mark_deleted(user_id: int) -> None:
    client = get_redis_client()
    if client is None:
        return
    try:
        await client.set(
            _key(user_id),
            str(int(time.time())).encode(),
            ex=settings.access_token_expires_seconds + _GRACE_S,
        )
    except Exception as error:  # noqa: BLE001 — the account is closed either way
        _log.warning(
            "closed_account_not_marked", user_id=user_id, error=str(error)[:200]
        )


async def issued_before_closing(user_id: int, issued_at: int | None) -> bool:
    """Whether a token of ``user_id`` issued at ``issued_at`` predates the
    account's closing. A token that does not say when it was issued counts as
    issued before."""
    client = get_redis_client()
    if client is None:
        return False
    try:
        closed_at = await client.get(_key(user_id))
    except Exception as error:  # noqa: BLE001 — fail open, see module docstring
        _log.warning("closed_account_unchecked", error=str(error)[:200])
        return False
    if closed_at is None:
        return False
    return issued_at is None or issued_at <= int(closed_at)


def _token(scope: Scope) -> str:
    headers = Headers(scope=scope)
    scheme, _, value = headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    # A browser cannot set a header on an iframe or a WebSocket.
    return QueryParams(scope.get("query_string", b"")).get("token") or ""


class ClosedAccountGuard:
    """Pure ASGI: refuses any request or WebSocket whose access token names a
    closed account."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            token = _token(scope)
            claims = verify_access_token(token) if token else None
            if claims is not None and claims.user_id is not None:
                if await issued_before_closing(claims.user_id, claims.issued_at):
                    await _refuse(scope, receive, send)
                    return
        await self.app(scope, receive, send)


async def _refuse(scope: Scope, receive: Receive, send: Send) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 4401})
        return
    body: dict[str, Any] = format_error_response(
        401, "This account has been closed", name="AccountClosed"
    )
    await JSONResponse(body, status_code=401)(scope, receive, send)
