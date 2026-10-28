"""Who is editing an agent right now — a courtesy lease, not a lock.

What actually stops two people overwriting each other is the version on the row
(``AgentInstance.version``): a save built from a stale version is refused no
matter what this module says. This is the other half — telling the second person
*before* they start that a teammate is already open, so they do not spend five
minutes on a change that will be refused anyway.

Deliberately soft. The lease lives in Valkey with a TTL, so a closed tab, a
sleeping laptop or a killed process frees it by itself: there is no cleanup pass
and no stuck lock to unlock. And when Valkey is not configured — tests, a
deployment that runs without it — every read says nobody is editing and every
take succeeds: the version check is the answer that has to hold, and a courtesy
that cannot be written must not be the thing that refuses a save.
"""

from __future__ import annotations

import uuid

from app.core.redis import get_redis_client

#: How long a lease lives without being renewed. Long enough to write a few
#: lines of instruction, short enough that a closed tab is not a lasting claim.
LEASE_SECONDS = 120


def _key(agent_id: uuid.UUID) -> str:
    return f"agent-editing:{agent_id}"


async def holder(agent_id: uuid.UUID) -> str | None:
    """The handle editing this agent, or None — including when it cannot be known.

    None is the honest answer for no Valkey, an unreachable one and an expired
    lease alike: all three mean "nobody we can point at", and the screen shows
    no 「正在编辑」 rather than a wrong name.
    """
    client = get_redis_client()
    if client is None:
        return None
    try:
        value = await client.get(_key(agent_id))
    except Exception:
        return None
    if value is None:
        return None
    # The client is built without ``decode_responses``.
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


async def take(agent_id: uuid.UUID, handle: str) -> bool:
    """Claim the lease for ``handle``, renewing it when they already hold it.

    False when somebody else holds it. True when Valkey cannot be reached, for
    the same reason :func:`holder` answers None: a courtesy that failed to write
    must not be what refuses a save.
    """
    client = get_redis_client()
    if client is None:
        return True
    try:
        if await client.set(_key(agent_id), handle, nx=True, ex=LEASE_SECONDS):
            return True
        if await holder(agent_id) == handle:
            await client.set(_key(agent_id), handle, ex=LEASE_SECONDS)
            return True
        return False
    except Exception:
        return True


async def released(agent_id: uuid.UUID, handle: str) -> None:
    """Give the lease up, if ``handle`` is the one still holding it."""
    client = get_redis_client()
    if client is None:
        return
    try:
        if await holder(agent_id) == handle:
            await client.delete(_key(agent_id))
    except Exception:
        return
