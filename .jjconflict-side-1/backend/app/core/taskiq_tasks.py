import logging

from taskiq.schedule_sources import LabelScheduleSource

from app.core.taskiq_broker import broker, scheduler
from app.db.session import AsyncSessionLocal as async_session_factory

logger = logging.getLogger(__name__)


@broker.task(
    task_name="notification_aggregation_finalize",
    schedule=[{"cron": "* * * * *"}],
)
async def notification_aggregation_finalize_task() -> dict[str, int]:
    """Finalize expired notification aggregations. Runs every minute."""
    from app.domain.notification.publisher import build_notification_event_handler

    async with async_session_factory() as session:
        handler = build_notification_event_handler(session)
        finalized = await handler.finalize_expired()
        await session.commit()
        count = len(finalized)
        if count > 0:
            logger.info("Finalized %d notification aggregations", count)
        return {"finalized": count}


@broker.task(
    task_name="task_deadline_check",
    schedule=[{"cron": "*/15 * * * *"}],
)
async def task_deadline_check_task() -> dict[str, int]:
    """Check and fail expired task deadlines. Runs every 15 minutes."""
    from app.domain.task.deadline_scheduler import check_and_fail_expired_deadlines

    async with async_session_factory() as session:
        count = await check_and_fail_expired_deadlines(session)
        return {"failed": count}


@broker.task(task_name="send_notification_email")
async def send_notification_email_task(
    recipient_email: str,
    subject: str,
    body_html: str,
    body_text: str | None = None,
) -> dict[str, bool]:
    """Send a single notification email."""
    from app.core.email import get_email_sender

    sender = get_email_sender()
    success = await sender.send(
        to=recipient_email,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
    )
    return {"success": success}


@broker.task(
    task_name="process_email_queue",
    schedule=[{"cron": "* * * * *"}],
)
async def process_email_queue_task() -> dict[str, int]:
    """Deliver queued email with claim/ack and bounded retry semantics."""
    import json

    from redis.asyncio import Redis

    from app.core.config import settings
    from app.core.email import get_email_sender
    from app.db.session import AsyncSessionLocal as async_session_factory

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

        async with async_session_factory() as session:
            from sqlalchemy import select

            from app.domain.user.models import User

            for _ in range(batch_count):
                claimed = await redis.lmove(queue_key, processing_key, "LEFT", "RIGHT")
                if claimed is None:
                    break
                item_str = claimed.decode() if isinstance(claimed, bytes) else claimed

                item: dict = {}
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

        if processed > 0:
            logger.info("Processed %d notification emails", processed)
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


scheduler.sources = [LabelScheduleSource(broker)]  # type: ignore[misc]
