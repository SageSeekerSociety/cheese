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
    success = sender.send(
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
    """Process pending notification emails from Redis queue. Runs every minute."""
    import json

    from redis.asyncio import Redis

    from app.core.config import settings
    from app.core.email import get_email_sender
    from app.db.session import AsyncSessionLocal as async_session_factory

    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    queue_key = settings.notification_email_queue_key
    batch_size = settings.notification_email_batch_size
    sender = get_email_sender()
    processed = 0

    try:
        async with async_session_factory() as session:
            from sqlalchemy import select
            from app.domain.user.models import User

            while True:
                items = await redis.lpop(queue_key, batch_size)
                if not items:
                    break

                for item_str in items:
                    try:
                        item = json.loads(item_str)
                        recipient_id = item.get("recipientId")
                        if not recipient_id:
                            continue

                        stmt = select(User.email).where(User.id == recipient_id)
                        result = await session.execute(stmt)
                        email = result.scalar_one_or_none()
                        if not email:
                            continue

                        notification_type = item.get("type", "notification")
                        subject = f"[Cheese] {notification_type}"
                        body_html = f"<p>You have a new notification: {notification_type}</p>"

                        sender.send(
                            to=email,
                            subject=subject,
                            body_html=body_html,
                        )
                        processed += 1
                    except Exception:
                        logger.exception("Failed to process email queue item")

        if processed > 0:
            logger.info("Processed %d notification emails", processed)
    finally:
        await redis.aclose()

    return {"processed": processed}


scheduler.sources = [LabelScheduleSource(broker)]
