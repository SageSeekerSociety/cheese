from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.notification.handlers import (
    InAppNotificationHandler,
    NotificationEventHandler,
)
from app.domain.notification.outbox import ChannelIntentHandler


def build_notification_event_handler(session: AsyncSession) -> NotificationEventHandler:
    return NotificationEventHandler(
        session=session,
        channel_handlers=[
            InAppNotificationHandler(session),
            ChannelIntentHandler(session, push_enabled=settings.web_push_configured),
        ],
    )
