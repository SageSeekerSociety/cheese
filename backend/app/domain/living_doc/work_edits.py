"""What a 芝士 changed in a document while answering one question.

Noted under the answer's work id as the changes land, so whoever is waiting on
the answer (the selection box) can show them and undo them once it is done.
"""

import json

from redis.asyncio import Redis

#: How long a noted change waits for its answer to collect it.
KEEP_S = 3600


def _key(work: str) -> str:
    return f"doc-agent:edits:{work}"


async def record(redis: Redis | None, work: str, edits: list[dict]) -> None:
    if redis is None or not work or not edits:
        return
    name = _key(work)
    await redis.rpush(name, *(json.dumps(edit, ensure_ascii=False) for edit in edits))
    await redis.expire(name, KEEP_S)


async def take(redis: Redis, work: str) -> list[dict]:
    """What was changed while answering ``work``, in order, and forgotten."""
    name = _key(work)
    raw = await redis.lrange(name, 0, -1)
    await redis.delete(name)
    return [json.loads(item) for item in raw]
