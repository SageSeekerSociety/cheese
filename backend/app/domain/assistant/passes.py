"""What lets a person's 芝士 through a door its gate opened.

A person's 芝士 acts through the routes the person would use by hand, as the
person. A route that does what others see is not opened to its credential
outright (``api.auth.GATED_ROUTES``): it takes a pass the gate issued
(``assistant.gate``) for this answer, this route and this place, once. So the
gate cannot be stepped around: nothing reaches those routes but what it let
through, and only as many times as it said.
"""

from __future__ import annotations

import json
import secrets

from app.core.redis import get_redis_client

#: How long a pass waits to be used: the calls follow the gate at once.
PASS_S = 120


def _key(token: str) -> str:
    return f"assistant:pass:{token}"


async def issue(work: str, doors: list[tuple[str, str | None]]) -> str | None:
    """A pass for these calls of answer ``work``: each is a route (its
    endpoint's dotted name) and the place it acts in (a project's, a channel's
    or a task's id), or None where the route names none. None when there is no
    Valkey to keep it, and so no way to act."""
    redis = get_redis_client()
    if redis is None:
        return None
    token = secrets.token_urlsafe(24)
    await redis.set(
        _key(token),
        json.dumps({"work": work, "doors": [list(d) for d in doors]}),
        ex=PASS_S,
    )
    return token


async def use(token: str, *, work: str, endpoint: str, places: set[str]) -> bool:
    """Whether ``token`` lets answer ``work`` through ``endpoint`` at one of
    ``places`` (the ids the route acts on); a door it opens is used up."""
    redis = get_redis_client()
    if redis is None or not token:
        return False
    key = _key(token)
    raw = await redis.get(key)
    if raw is None:
        return False
    held = json.loads(raw)
    if held.get("work") != work:
        return False
    doors = held.get("doors") or []
    for i, (door, place) in enumerate(doors):
        if door == endpoint and (place is None or place in places):
            rest = doors[:i] + doors[i + 1 :]
            if rest:
                held["doors"] = rest
                await redis.set(key, json.dumps(held), keepttl=True)
            else:
                await redis.delete(key)
            return True
    return False
