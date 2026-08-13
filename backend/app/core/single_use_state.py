"""Redeem-once tokens, backed by Valkey/Redis.

A signed state is a bearer credential: whoever holds the string is treated as
whoever it names. That is fine while it stays in one browser and fatal the
moment it does not — and users forward these links, because the natural thing
to do when an OAuth flow fails is to paste the URL into a group chat and ask
for help. #222 is that exact story: 小鱼儿's account-link state reached a
different logged-in browser, which completed the callback in her name. She was
lucky — that browser's GitHub was already linked, so it failed closed. Had it
not been, a stranger's GitHub identity would have been bound to her platform
account, with neither of them told.

Signature and TTL cannot help here: a forwarded state is perfectly signed and
perfectly fresh. Only *state* can — the server has to remember that this one
was already spent. So the mint side reserves the token's ``jti`` and the
callback claims it; a `DELETE` returning 1 means "I am the one who spent it",
and it can return 1 only once no matter how many callbacks race.

**Fail closed.** A dedup cache may degrade to "allow" when Redis is down; a
single-use guarantee may not, because degrading it to "allow" silently removes
the protection at precisely the moment nobody is watching. This costs nothing
in practice: login and 2FA already need Redis, so a deployment that cannot
reach it has no logged-in users to link accounts for.
"""

import logging

from app.core.redis import get_redis_client

_log = logging.getLogger(__name__)

_NAMESPACE = "cheese:once"


class SingleUseUnavailableError(RuntimeError):
    """Redis could not be reached, so single use cannot be guaranteed.

    Callers must treat this as a refusal, never as a pass — see the module
    docstring on why this one fails closed.
    """


def _key(scope: str, jti: str) -> str:
    return f"{_NAMESPACE}:{scope}:{jti}"


async def reserve(scope: str, jti: str, *, ttl_s: int) -> None:
    """Record that ``jti`` may be spent once, within ``ttl_s`` seconds.

    The TTL should match the token's own expiry: a key that outlives its token
    is dead weight, and one that dies first would let an unexpired token be
    replayed.
    """
    client = get_redis_client()
    if client is None:
        raise SingleUseUnavailableError(f"redis not configured (scope={scope})")
    try:
        await client.set(_key(scope, jti), b"1", ex=ttl_s, nx=True)
    except Exception as exc:  # noqa: BLE001 — any transport failure is a refusal
        raise SingleUseUnavailableError(f"redis unreachable (scope={scope})") from exc


async def claim(scope: str, jti: str) -> bool:
    """Spend ``jti``. True exactly once; False if already spent or expired.

    `DELETE` is the whole concurrency story — it is atomic, so two callbacks
    arriving together cannot both see a 1.
    """
    client = get_redis_client()
    if client is None:
        raise SingleUseUnavailableError(f"redis not configured (scope={scope})")
    try:
        removed = await client.delete(_key(scope, jti))
    except Exception as exc:  # noqa: BLE001 — any transport failure is a refusal
        raise SingleUseUnavailableError(f"redis unreachable (scope={scope})") from exc
    return bool(removed)
