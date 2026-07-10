from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.cx_notification.models import Notification


@dataclass
class ResolvedEntityInfoDTO:
    id: str
    type: str
    name: str
    url: str | None = None
    avatarUrl: str | None = None
    status: str | None = None


@dataclass
class NotificationDTO:
    id: int
    type: str
    read: bool
    createdAt: int
    entities: dict[str, ResolvedEntityInfoDTO | None]
    contextMetadata: dict[str, Any]

    @classmethod
    def from_notification(
        cls,
        notification: Notification,
        metadata_map: dict[str, Any],
        resolved_entities: dict[str, ResolvedEntityInfoDTO | None],
    ) -> "NotificationDTO":
        entities = dict(resolved_entities)

        context_meta: dict[str, Any] = {}
        for key, value in metadata_map.items():
            stripped = cls._strip_entity_nodes(value)
            if stripped is not None:
                context_meta[key] = stripped

        created_at_ms: int = (
            int(notification.created_at.timestamp() * 1000)
            if getattr(notification, "created_at", None) is not None
            else 0
        )

        notification_type = notification.type
        type_str = (
            notification_type.value
            if hasattr(notification_type, "value")
            else str(notification_type)
        )

        return cls(
            id=notification.id,
            type=type_str,
            read=notification.read,
            createdAt=created_at_ms,
            entities=entities,
            contextMetadata=context_meta,
        )

    @staticmethod
    def _strip_entity_nodes(value: Any) -> Any:
        if isinstance(value, Mapping):
            entity_type = value.get("type")
            entity_id = value.get("id")
            if isinstance(entity_type, str) and isinstance(entity_id, str):
                return None
            cleaned: dict[str, Any] = {}
            for key, nested in value.items():
                stripped = NotificationDTO._strip_entity_nodes(nested)
                if stripped is not None:
                    cleaned[key] = stripped
            return cleaned

        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            cleaned_list = []
            for nested in value:
                stripped = NotificationDTO._strip_entity_nodes(nested)
                if stripped is not None:
                    cleaned_list.append(stripped)
            return cleaned_list

        return value
