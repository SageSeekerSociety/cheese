from dataclasses import dataclass
from typing import Any

from app.domain.cx_notification.models import NotificationType


@dataclass(slots=True)
class NotificationTriggerEvent:
    source: str
    recipient_ids: set[int]
    type: NotificationType
    payload: dict[str, Any]
    actor_id: int | None = None
