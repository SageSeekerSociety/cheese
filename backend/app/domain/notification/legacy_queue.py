"""Migration drain for Redis items written by the previous release.

New producers never write these keys. Use the previous consumer's lock while
moving pending/processing items into uniquely identified staging records. SQL
commit precedes Redis acknowledgement, so replaying staging cannot duplicate an
intent. Expired old claims retain the old external at-least-once guarantee:
provider acceptance immediately before a lost acknowledgement can be repeated.
"""

import json
import logging
import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy.dialects.postgresql import insert

from app.core.config import settings
from app.domain.delivery.models import ChannelDelivery

logger = logging.getLogger(__name__)
_STAGE = """
if redis.call('GET', KEYS[4]) ~= ARGV[2] then return false end
local staged = redis.call('LINDEX', KEYS[3], 0)
if staged then return staged end
local item = redis.call('LPOP', KEYS[2]) or redis.call('LPOP', KEYS[1])
if not item then return false end
local envelope = cjson.encode({id=ARGV[1], raw=item})
redis.call('RPUSH', KEYS[3], envelope)
return envelope
"""


async def import_legacy_queue(sessions, *, channel, batch_size, redis=None):
    owned = redis is None
    redis = redis or Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    key = getattr(settings, f"notification_{channel}_queue_key")
    staging = f"{key}:sql-migration"
    lock_key = f"{key}:consumer-lock"
    lock = redis.lock(lock_key, timeout=90)
    acquired = False
    imported = 0
    try:
        acquired = await lock.acquire(blocking=False)
        if not acquired:
            return 0
        for _ in range(batch_size):
            await lock.extend(90, replace_ttl=True)
            envelope = await redis.eval(
                _STAGE,
                4,
                key,
                f"{key}:processing",
                staging,
                lock_key,
                str(uuid.uuid4()),
                lock.local.token,
            )
            if not envelope:
                break
            entry = json.loads(envelope)
            try:
                item = json.loads(entry["raw"])
                recipient = int(item["recipientId"])
                attempts = int(item.get(f"_{channel}Retry") or 0)
            except (ValueError, KeyError, TypeError):
                # Preserve malformed legacy input for operator inspection.
                async with redis.pipeline(transaction=True) as pipe:
                    pipe.rpush(f"{key}:dead", entry["raw"])
                    pipe.lrem(staging, 1, envelope)
                    await pipe.execute()
                continue
            async with sessions() as session:
                await session.execute(
                    insert(ChannelDelivery)
                    .values(
                        id=uuid.UUID(entry["id"]),
                        delivery_key=f"legacy:{entry['id']}",
                        channel=channel,
                        receiver_id=recipient,
                        payload=item,
                        recorded_at=datetime.now(UTC),
                        attempts=attempts,
                    )
                    .on_conflict_do_nothing(constraint="uq_delivery_channel")
                )
                await session.commit()
            # A crash here leaves the stable envelope for the next tick.
            await redis.lrem(staging, 1, envelope)
            imported += 1
    finally:
        if acquired:
            try:
                await lock.release()
            except Exception:
                logger.exception("Legacy notification migration lost its lease")
        if owned:
            await redis.aclose()
    return imported
