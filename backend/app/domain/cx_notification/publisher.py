from collections.abc import Iterable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import get_redis_client
from app.domain.cx_notification.dedup import NotificationDeduplicator
from app.domain.cx_notification.events import NotificationTriggerEvent
from app.domain.cx_notification.handlers import (
    InAppNotificationHandler,
    NotificationEventHandler,
    RedisEmailQueueNotificationHandler,
)
from app.domain.cx_notification.models import NotificationType


def build_notification_event_handler(session: AsyncSession) -> NotificationEventHandler:
    redis_client = get_redis_client()
    deduplicator = None
    if redis_client is not None:
        deduplicator = NotificationDeduplicator(
            redis_client,
            ttl_seconds=settings.notification_dedup_ttl_seconds,
        )

    channel_handlers = [
        InAppNotificationHandler(session=session),
        RedisEmailQueueNotificationHandler(
            redis_client,
            queue_key=settings.notification_email_queue_key,
            batch_size=settings.notification_email_batch_size,
        ),
    ]

    return NotificationEventHandler(
        session=session,
        deduplicator=deduplicator,
        channel_handlers=channel_handlers,
    )


async def publish_notification_event(
    session: AsyncSession,
    *,
    recipient_ids: Iterable[int],
    type_: NotificationType,
    payload: dict[str, Any],
    actor_id: int | None = None,
    source: str = "python-backend",
) -> None:
    recipients = {int(r) for r in recipient_ids if int(r) > 0}
    if not recipients:
        return
    event = NotificationTriggerEvent(
        source=source,
        recipient_ids=recipients,
        type=type_,
        payload=payload,
        actor_id=actor_id,
    )
    handler = build_notification_event_handler(session)
    await handler.handle(event)
