"""While a person's question is being answered.

One question at a time per conversation: the question holds its conversation in
Valkey until it is answered, or ``BUSY_SECONDS`` have passed (a crashed worker
must not lock it for good). The same hold is what lets that conversation's
芝士 reach the model at all (``api/routes/llm_proxy.py``): a model call is
charged to the person, and only a question they asked may be charged to them.
"""

import uuid

from redis.asyncio import Redis

#: How long one question may hold its conversation before another is let in
#: even without the first one finishing.
BUSY_SECONDS = 180


def busy_key(conversation_id: uuid.UUID | str) -> str:
    return f"assistant:busy:{conversation_id}"


async def hold(redis: Redis, conversation_id: uuid.UUID) -> bool:
    """Take the conversation for one question; False while another holds it."""
    return bool(
        await redis.set(busy_key(conversation_id), "1", nx=True, ex=BUSY_SECONDS)
    )


async def release(redis: Redis, conversation_id: uuid.UUID) -> None:
    await redis.delete(busy_key(conversation_id))


async def answering(redis: Redis, conversation_id: uuid.UUID | str) -> bool:
    """Is a question of this conversation being answered right now?"""
    return bool(await redis.exists(busy_key(conversation_id)))
