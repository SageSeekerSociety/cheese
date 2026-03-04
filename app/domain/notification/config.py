from __future__ import annotations

from datetime import timedelta

from app.domain.notification.models import NotificationType


class NotificationConfig:
    def __init__(self) -> None:
        self.aggregatable_types: set[NotificationType] = {NotificationType.REACTION}
        self.aggregation_window: timedelta = timedelta(minutes=10)

    def is_aggregatable(self, type_: NotificationType) -> bool:
        return type_ in self.aggregatable_types


notification_config = NotificationConfig()
