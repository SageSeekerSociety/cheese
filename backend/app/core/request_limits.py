"""Per-client request limits: a request rate and a number in flight.

One open page once took more than half of this process's request time on a
shared deployment. Every limit here is per *principal* — whoever the request
verifiably comes from — so one client running hot slows only itself.

Two limits, checked in this order:

- **rate** (GCRA in Redis): a sustained rate with a burst on top. Shared by
  every process that reads the same Redis. A Redis that is down or slow lets
  the request through: a broken brake must not be a broken platform.
- **concurrency** (in this process): how many of the principal's requests may
  be running at once. The rest wait in line, first come first served, for a
  bounded time; a full line, or a wait that runs out, is refused.

A refusal is a 429 that says which policy it hit, in the header syntax of
draft-ietf-httpapi-ratelimit-headers-11. WebSockets are not limited.
"""

import asyncio
import base64
import binascii
import ipaddress
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.common.auth import verify_access_token
from app.core.client_address import resolved_client_address
from app.core.config import settings
from app.core.errors import format_error_response
from app.core.metrics import registry
from app.core.obs import get_logger
from app.core.redis import get_redis_client
from app.core.sandbox_auth import (
    is_global_sandbox_token,
    project_agent_claims,
    scoped_token_claims,
)

_log = get_logger("app.request_limits")

QUOTA_EXCEEDED = "https://iana.org/assignments/http-problem-types#quota-exceeded"
RATE = "rate"
CONCURRENCY = "concurrency"

# Infrastructure probes. Counting them would refuse the health check of a
# process that is busy, which is the moment it matters most.
_EXEMPT = ("/health", "/healthz", "/metrics")

# Probes that are exempt only when they come from inside: the container's own
# healthcheck on loopback, or the rollout's curl through the published port,
# which reaches the app from a trusted proxy hop with no client in front of
# it. `/readyz` is public, so a request that carries an outside client's
# address is counted like any other — exempting it too would hand anyone an
# unlimited route.
_EXEMPT_FROM_INSIDE = ("/readyz",)

# GCRA over one key: the stored value is the theoretical arrival time (TAT) in
# milliseconds. Integers throughout — Redis turns a Lua number into a string
# with 14 significant digits, and a millisecond timestamp already uses 13.
_GCRA = """
local now = tonumber(ARGV[1])
local interval = tonumber(ARGV[2])
local burst = tonumber(ARGV[3])
local tat = tonumber(redis.call('GET', KEYS[1]) or now)
if tat < now then tat = now end
local allow_at = tat + interval - burst * interval
if now < allow_at then
  return {0, 0, allow_at - now, tat - now}
end
redis.call('SET', KEYS[1], tat + interval, 'PX', tat + interval - now)
return {1, math.floor((now - allow_at) / interval), 0, tat + interval - now}
"""


@dataclass(frozen=True)
class _RateAnswer:
    allowed: bool
    remaining: int
    retry_after_ms: int
    # Until the whole burst is available again.
    reset_ms: int


def principal_of(scope: Scope) -> tuple[str, str] | None:
    """(kind, key) for the request, cheapest verified identity first; None when
    nothing identifies it and its address is not its own.

    Only a credential that verifies gets a bucket of its own. Anything else —
    a forged or expired token included — is counted by the address it came
    from, or a made-up token would be a fresh quota.
    """
    headers = Headers(scope=scope)
    authorization = headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    bearer = value.strip() if scheme.lower() == "bearer" else ""
    claims = verify_access_token(bearer) if bearer else None
    if claims is not None:
        return "user", f"user:{claims.handle}"
    # A sandbox credential arrives wherever its client puts it: the `cheese`
    # CLI as X-Cheese-Token, Claude Code through the LLM proxy as a bearer or
    # x-api-key, git through the forge relay as the Basic password.
    candidates = [
        headers.get("x-cheese-token", ""),
        bearer,
        headers.get("x-api-key", ""),
    ]
    if scheme.lower() == "basic":
        candidates.append(_basic_password(value.strip()))
    tokens = [token.strip() for token in candidates if token.strip()]
    for token in tokens:
        key = _agent_key(token)
        if key is not None:
            return "agent", key
    # The signing secret, used directly by the host's own timers and by dev
    # tooling. It cannot be forged, so it is one principal of its own rather
    # than whoever shares its address.
    if any(is_global_sandbox_token(token) for token in tokens):
        return "infra", "infra:sandbox-secret"
    address = resolved_client_address(Request(scope))
    if address is None:
        return None
    return "ip", f"ip:{address}"


def _from_inside(scope: Scope) -> bool:
    """Whether no outside client stands behind the request: the address cannot
    be told from a proxy's, or it is this host's own loopback."""
    address = resolved_client_address(Request(scope))
    if address is None:
        return True
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def _agent_key(token: str) -> str | None:
    """Who a valid sandbox credential acts as, within its project.

    Keyed by project AND agent: one agent handle can act in several projects,
    and those are separate workloads. A token naming no agent is a room's
    platform capability (or a project-wide one), counted per room."""
    claims = scoped_token_claims(token)
    if claims is not None:
        project = claims.get("p")
        agent = claims.get("a")
        if agent:
            return f"agent:{project}:{agent}"
        topic = claims.get("t")
        return f"agent:{project}:room:{topic}" if topic else f"agent:{project}"
    credential = project_agent_claims(token)
    if credential is not None:
        return f"agent:{credential.project_id}:credential"
    return None


def _basic_password(value: str) -> str:
    try:
        return base64.b64decode(value, validate=True).decode().partition(":")[2]
    except (ValueError, UnicodeError, binascii.Error):
        return ""


class _RedisWarning:
    """At most one warning a minute while Redis cannot be asked."""

    def __init__(self) -> None:
        self._last = -math.inf

    def say(self, error: BaseException) -> None:
        now = time.monotonic()
        if now - self._last < 60:
            return
        self._last = now
        _log.warning(
            "request_rate_unchecked",
            error=type(error).__name__,
            detail=str(error)[:200],
        )


_redis_warning = _RedisWarning()


async def _check_rate(key: str) -> _RateAnswer | None:
    """The GCRA answer for one request of ``key``, or None when Redis could
    not give one (the request then goes through)."""
    client = get_redis_client()
    if client is None:
        return None
    interval = max(1, round(1000 / settings.request_rate_per_s))
    try:
        script = client.register_script(_GCRA)
        answer = await asyncio.wait_for(
            script(
                keys=[f"request-rate:{key}"],
                args=[int(time.time() * 1000), interval, settings.request_rate_burst],
            ),
            timeout=settings.request_limit_redis_timeout_s,
        )
    except Exception as error:  # noqa: BLE001 — fail open, said once a minute
        _redis_warning.say(error)
        return None
    allowed, remaining, retry_after_ms, reset_ms = (int(v) for v in answer)
    return _RateAnswer(bool(allowed), remaining, retry_after_ms, reset_ms)


@dataclass
class _Line:
    running: int = 0
    waiting: deque[asyncio.Future[None]] = field(default_factory=deque)


class ConcurrencyLimiter:
    """At most N running per key; the rest wait in arrival order.

    A slot is handed straight from the request that frees it to the first one
    waiting, so a newcomer can never overtake the line."""

    def __init__(self) -> None:
        self._lines: dict[str, _Line] = {}

    async def acquire(
        self, key: str, *, limit: int, depth: int, timeout: float
    ) -> bool:
        line = self._lines.setdefault(key, _Line())
        if line.running < limit and not line.waiting:
            line.running += 1
            return True
        if len(line.waiting) >= depth:
            self._forget_if_idle(key, line)
            return False
        turn: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        line.waiting.append(turn)
        try:
            await asyncio.wait({turn}, timeout=timeout)
        except asyncio.CancelledError:
            self._leave(key, line, turn)
            raise
        if turn.done():
            return True
        self._leave(key, line, turn)
        return False

    def release(self, key: str) -> None:
        line = self._lines[key]
        while line.waiting:
            turn = line.waiting.popleft()
            if not turn.done():
                turn.set_result(None)  # the slot passes on; `running` is unchanged
                return
        line.running -= 1
        self._forget_if_idle(key, line)

    def _leave(self, key: str, line: _Line, turn: asyncio.Future[None]) -> None:
        if turn.done():
            # Handed a slot at the very moment it stopped waiting: pass it on.
            self.release(key)
            return
        turn.cancel()
        line.waiting.remove(turn)
        self._forget_if_idle(key, line)

    def _forget_if_idle(self, key: str, line: _Line) -> None:
        if line.running == 0 and not line.waiting:
            self._lines.pop(key, None)


def _policy_header() -> str:
    burst = settings.request_rate_burst
    window = max(1, math.ceil(burst / settings.request_rate_per_s))
    return (
        f'"{RATE}";q={burst};w={window}, '
        f'"{CONCURRENCY}";q={settings.request_concurrency};qu="concurrent-requests"'
    )


def _seconds(ms: int) -> int:
    return max(0, math.ceil(ms / 1000))


def _rate_item(rate: _RateAnswer) -> str:
    return f'"{RATE}";r={rate.remaining};t={_seconds(rate.reset_ms)}'


def _refusal(policy: str, retry_after: int, path: str, kind: str) -> JSONResponse:
    """The 429. Its `RateLimit` names only the policy that refused, whose
    window is exactly `Retry-After` — the draft has the server never send a
    `Retry-After` earlier than a window it reports alongside."""
    registry.counter("http_requests_refused_total", {"policy": policy}).inc()
    _log.warning("request_refused", principal=kind, policy=policy, path=path)
    if policy == RATE:
        message = "Too many requests. Try again in a few seconds"
    else:
        message = "Too many requests at once. Try again in a moment"
    body: dict[str, Any] = format_error_response(
        429, message, name="QuotaExceededError"
    )
    body["error"]["retryable"] = True
    body["error"]["data"] = {"violated-policies": [policy], "retry-after": retry_after}
    body["type"] = QUOTA_EXCEEDED
    body["violated-policies"] = [policy]
    item = f'"{RATE}";r=0;t={retry_after}' if policy == RATE else f'"{CONCURRENCY}";r=0'
    headers = {
        "Retry-After": str(retry_after),
        "RateLimit-Policy": _policy_header(),
        "RateLimit": item,
    }
    return JSONResponse(body, status_code=429, headers=headers)


class RequestLimits:
    """Pure ASGI, so a slot is held for exactly as long as the response is
    being produced — streamed bodies included — and released however the
    request ends."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.concurrency = ConcurrencyLimiter()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        if path in _EXEMPT or path.startswith("/health/"):
            await self.app(scope, receive, send)
            return
        if path in _EXEMPT_FROM_INSIDE and _from_inside(scope):
            await self.app(scope, receive, send)
            return
        principal = principal_of(scope)
        if principal is None:
            await self.app(scope, receive, send)
            return
        kind, key = principal
        rate = await _check_rate(key)
        if rate is not None and not rate.allowed:
            retry_after = max(1, _seconds(rate.retry_after_ms))
            await _refusal(RATE, retry_after, path, kind)(scope, receive, send)
            return
        admitted = await self.concurrency.acquire(
            key,
            limit=settings.request_concurrency,
            depth=settings.request_queue_depth,
            timeout=settings.request_queue_timeout_s,
        )
        if not admitted:
            await _refusal(CONCURRENCY, 1, path, kind)(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw = list(message.get("headers", []))
                raw.append((b"ratelimit-policy", _policy_header().encode()))
                if rate is not None:
                    raw.append((b"ratelimit", _rate_item(rate).encode()))
                message = {**message, "headers": raw}
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            self.concurrency.release(key)
