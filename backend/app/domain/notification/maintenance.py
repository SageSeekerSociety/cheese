"""The two notification jobs that only a clock can start.

Everything else in this domain runs on the request that caused it: a mention
publishes, a handler writes the in-app row and pushes the email onto Redis.
These two have no such caller.

``finalize_expired_aggregations`` closes an aggregation window. A burst of
mentions is merged into one notification that stays open for
``notification_config.aggregation_window``; the merged notification is only
DELIVERED when that window is finalized, so without this the aggregated ones
are written and never sent — the exact notifications a busy room produces most
of.

``drain_email_queue`` is the only consumer of the Redis list every email
notification is pushed onto. Nothing else reads that key, so an unrun drain is
not a delay: it is a queue that grows forever and an inbox that never receives.
"""

import json
import logging
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionFactory
from app.core.email import get_email_sender
from app.domain.notification.publisher import build_notification_event_handler
from app.domain.user.models import User

logger = logging.getLogger(__name__)


async def finalize_expired_aggregations(sessions: SessionFactory) -> dict[str, int]:
    """Close every aggregation window that has expired, delivering what it held."""
    async with sessions() as session:
        handler = build_notification_event_handler(session)
        finalized = await handler.finalize_expired()
        await session.commit()
    return {"finalized": len(finalized)}


async def drain_email_queue(sessions: SessionFactory) -> dict[str, int]:
    """Deliver queued email with claim/ack and bounded retry semantics."""
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    queue_key = settings.notification_email_queue_key
    processing_key = f"{queue_key}:processing"
    dead_letter_key = f"{queue_key}:dead"
    lock_key = f"{queue_key}:consumer-lock"
    batch_size = settings.notification_email_batch_size
    max_retries = max(1, settings.notification_email_max_retries)
    sender = get_email_sender()
    processed = 0
    retried = 0
    dead_lettered = 0
    lock = redis.lock(lock_key, timeout=90)
    lock_acquired = False

    try:
        lock_acquired = await lock.acquire(blocking=False)
        if not lock_acquired:
            logger.info("Email queue consumer is already running")
            return {"processed": 0, "retried": 0, "dead_lettered": 0}

        # A process can die after LMOVE and before acknowledgement. Once its
        # lock expires, the next sole consumer puts those claims back before
        # taking new work. SMTP is therefore at-least-once, never at-most-once.
        while await redis.lmove(processing_key, queue_key, "LEFT", "RIGHT"):
            pass
        batch_count = min(batch_size, await redis.llen(queue_key))

        async with sessions() as session:
            for _ in range(batch_count):
                claimed = await redis.lmove(queue_key, processing_key, "LEFT", "RIGHT")
                if claimed is None:
                    break
                item_str = claimed.decode() if isinstance(claimed, bytes) else claimed

                item: dict[str, Any] = {}
                failure: str | None = None
                try:
                    decoded = json.loads(item_str)
                    if not isinstance(decoded, dict):
                        raise ValueError("email queue payload is not an object")
                    item = decoded
                    recipient_id = item.get("recipientId")
                    if not recipient_id:
                        raise ValueError("email queue payload has no recipientId")

                    stmt = select(User.email).where(User.id == recipient_id)
                    result = await session.execute(stmt)
                    email = result.scalar_one_or_none()
                    if not email:
                        raise ValueError("email recipient has no address")

                    notification_type = item.get("type", "notification")
                    subject = f"[Cheese] {notification_type}"
                    body_html = (
                        f"<p>You have a new notification: {notification_type}</p>"
                    )

                    sent = await sender.send(
                        to=email,
                        subject=subject,
                        body_html=body_html,
                    )
                    if not sent:
                        failure = "SMTP delivery returned false"
                except Exception as exc:  # noqa: BLE001 — retain for retry below
                    failure = f"{type(exc).__name__}: {exc}"

                if failure is None:
                    await redis.lrem(processing_key, 1, item_str)
                    processed += 1
                else:
                    retry_count = int(item.get("_emailRetry") or 0) + 1
                    item["_emailRetry"] = retry_count
                    item["_emailError"] = failure[:300]
                    destination = (
                        dead_letter_key if retry_count >= max_retries else queue_key
                    )
                    encoded = json.dumps(item, separators=(",", ":"))
                    pipe = redis.pipeline(transaction=True)
                    pipe.lrem(processing_key, 1, item_str)
                    pipe.rpush(destination, encoded)
                    await pipe.execute()
                    if destination == dead_letter_key:
                        dead_lettered += 1
                        logger.error(
                            "Email queue item moved to dead letter after "
                            "%d attempts: %s",
                            retry_count,
                            failure,
                        )
                    else:
                        retried += 1
                        logger.warning(
                            "Email delivery failed; retained for retry %d/%d: %s",
                            retry_count,
                            max_retries,
                            failure,
                        )
                await lock.extend(90, replace_ttl=True)
    finally:
        if lock_acquired:
            try:
                await lock.release()
            except Exception:  # noqa: BLE001 — TTL still prevents a stuck lock
                logger.exception("Failed to release email queue consumer lock")
        await redis.aclose()

    return {
        "processed": processed,
        "retried": retried,
        "dead_lettered": dead_lettered,
    }
