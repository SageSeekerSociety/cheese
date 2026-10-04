"""What GitHub last said about each App installation's hourly REST quota.

An installation has one quota for every call the platform makes with its
tokens, and GitHub reports it on every answer: the remaining count, the limit
and the reset time in the `x-ratelimit-*` headers, and, when the quota is gone,
a 403 or 429 that names when it comes back. This module keeps the last of those
per installation, read off every response a `forge_client` receives, so that
background work can stay away from an installation GitHub has just refused
until the time GitHub named, instead of finding out again on each call.

Tokens are tied to their installation when they are minted (`own`); a response
to a request carrying any other credential is not looked at.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

#: How long an installation stays closed after a quota refusal that names no
#: time to wait.
UNNAMED_WAIT_S = 60.0


@dataclass(frozen=True)
class Reading:
    """The quota GitHub reported on its latest answer for one installation."""

    remaining: int
    limit: int
    reset: float


_owners: dict[str, tuple[int, float]] = {}
_readings: dict[int, Reading] = {}
_refused_until: dict[int, float] = {}


def own(token: str, installation_id: int, expires_at: float) -> None:
    """Tie a freshly minted token to the installation whose quota it spends."""
    now = time.time()
    for stale in [t for t, (_, exp) in _owners.items() if exp <= now]:
        del _owners[stale]
    _owners[token] = (installation_id, expires_at)


def installation_of(request: httpx.Request) -> int | None:
    """The installation whose quota this request spends, when it carries a
    token the platform minted."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() not in ("bearer", "token"):
        return None
    owner = _owners.get(token.strip())
    return None if owner is None else owner[0]


def rate_limited(response: httpx.Response) -> bool:
    """Whether GitHub refused because a quota is spent.

    GitHub answers an exhausted quota with 403 (or 429) and says so in the
    headers; a 403 without them is a real permission refusal.
    """
    return response.status_code in (403, 429) and (
        response.headers.get("x-ratelimit-remaining") == "0"
        or "retry-after" in response.headers
    )


def wait_seconds(response: httpx.Response) -> float | None:
    """How long a quota refusal says to wait, when it says."""
    if retry_after := response.headers.get("retry-after"):
        return float(retry_after) if retry_after.isdigit() else None
    if (reset := response.headers.get("x-ratelimit-reset")) and reset.isdigit():
        return float(reset) - time.time()
    return None


def refused_until(installation_id: int) -> float | None:
    """The time before which GitHub has said this installation gets nothing."""
    until = _refused_until.get(installation_id)
    if until is None or until <= time.time():
        return None
    return until


def reading(installation_id: int) -> Reading | None:
    """The latest quota GitHub reported for this installation, while its hour
    has not reset."""
    latest = _readings.get(installation_id)
    if latest is None or latest.reset <= time.time():
        return None
    return latest


def record(installation_id: int, remaining: int, limit: int, reset: float) -> None:
    _readings[installation_id] = Reading(remaining, limit, reset)


def refuse(installation_id: int, wait_s: float | None) -> None:
    wait = wait_s if wait_s is not None and wait_s > 0 else UNNAMED_WAIT_S
    until = time.time() + wait
    _refused_until[installation_id] = max(
        _refused_until.get(installation_id, 0.0), until
    )


async def observe(response: httpx.Response) -> None:
    """Note what one GitHub answer says about its installation's quota."""
    installation_id = installation_of(response.request)
    if installation_id is None:
        return
    if rate_limited(response):
        refuse(installation_id, wait_seconds(response))
        return
    if response.headers.get("x-ratelimit-resource", "core") != "core":
        return
    remaining = response.headers.get("x-ratelimit-remaining", "")
    limit = response.headers.get("x-ratelimit-limit", "")
    reset = response.headers.get("x-ratelimit-reset", "")
    if remaining.isdigit() and limit.isdigit() and reset.isdigit():
        record(installation_id, int(remaining), int(limit), float(reset))
