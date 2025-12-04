from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.domain.notification.dto import NotificationDTO, ResolvedEntityInfoDTO
from app.domain.notification.entity_resolvers import EntityInfoResolver
from app.domain.notification.models import Notification, NotificationType
from app.domain.notification.repositories import NotificationRepository


@dataclass(slots=True)
class _EntityPointer:
    path: str
    type: str
    id: str


class NotificationQueryService:
    """Python port of notification query operations.

    NOTE: Initial version may not fully cover Kotlin behavior; will be aligned
    incrementally using contract tests.
    """

    def __init__(
        self,
        repo: NotificationRepository,
        resolvers: Sequence[EntityInfoResolver] | None = None,
    ) -> None:
        self._repo = repo
        self._resolver_map: dict[str, EntityInfoResolver] = {
            r.supported_entity_type(): r for r in (resolvers or [])
        }

    async def get_notification_by_id_for_current_user(
        self, user_id: int, notification_id: int
    ) -> Notification | None:
        return await self._repo.get_by_id_for_user(user_id=user_id, notification_id=notification_id)

    async def get_notifications_for_current_user(
        self,
        user_id: int,
        *,
        limit: int,
        cursor_created_at: datetime | None = None,
        cursor_id: int | None = None,
        type_: NotificationType | None = None,
        read: bool | None = None,
    ) -> Sequence[Notification]:
        return await self._repo.list_for_user(
            user_id=user_id,
            limit=limit,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
            type_=type_,
            read=read,
        )

    async def mark_all_as_read_for_current_user(self, user_id: int) -> int:
        return await self._repo.mark_all_as_read_for_user(user_id=user_id)

    async def set_read_status(
        self, user_id: int, notification_id: int, desired_read_status: bool
    ) -> int:
        return await self._repo.set_read_status_for_user(
            user_id=user_id, notification_id=notification_id, read=desired_read_status
        )

    async def get_unread_notification_count_for_current_user(self, user_id: int) -> int:
        return await self._repo.count_unread_for_user(user_id=user_id)

    async def count_notifications_for_current_user(
        self,
        user_id: int,
        *,
        type_: NotificationType | None = None,
        read: bool | None = None,
    ) -> int:
        """Return total count of notifications for pagination metadata."""
        return await self._repo.count_for_user(user_id=user_id, type_=type_, read=read)

    async def bulk_set_read_status(
        self, user_id: int, updates: Sequence[tuple[int, bool]]
    ) -> list[int]:
        """Bulk update read status for notifications owned by the user."""
        if not updates:
            return []

        ids = [id_ for id_, _ in updates]
        desired_map = dict(updates)

        notifications = await self._repo.find_all_by_ids_for_user(user_id=user_id, ids=ids)
        updated_ids: list[int] = []

        for notification in notifications:
            if notification.id is None:
                continue
            desired = desired_map.get(notification.id)
            if desired is not None and notification.read != desired:
                notification.read = desired
                updated_ids.append(notification.id)

        if updated_ids:
            await self._repo.save_all(notifications)

        return updated_ids

    async def delete_notification_for_current_user(self, user_id: int, notification_id: int) -> bool:
        return await self._repo.soft_delete_for_user(user_id=user_id, notification_id=notification_id)

    # --- Metadata / entity resolution ---

    async def resolve_entities_from_metadata(
        self, metadata_maps: Sequence[Mapping[str, Any]]
    ) -> dict[str, ResolvedEntityInfoDTO | None]:
        """Resolve nested entity references within metadata payloads."""

        pointers: list[_EntityPointer] = []
        for metadata in metadata_maps:
            for key, value in metadata.items():
                self._collect_entity_pointers(value, path=str(key), output=pointers)

        if not pointers:
            return {}

        ids_by_type: dict[str, set[str]] = {}
        for pointer in pointers:
            ids_by_type.setdefault(pointer.type, set()).add(pointer.id)

        resolved_by_type: dict[str, dict[str, ResolvedEntityInfoDTO | None]] = {}
        for entity_type, ids in ids_by_type.items():
            resolver = self._resolver_map.get(entity_type)
            if resolver is None:
                continue
            resolved_by_type[entity_type] = await resolver.resolve(sorted(ids))

        flattened: dict[str, ResolvedEntityInfoDTO | None] = {}
        for pointer in pointers:
            entity_map = resolved_by_type.get(pointer.type) or {}
            flattened[pointer.path] = entity_map.get(pointer.id)
        return flattened

    def _collect_entity_pointers(
        self,
        value: Any,
        *,
        path: str,
        output: list[_EntityPointer],
    ) -> None:
        if isinstance(value, Mapping):
            entity_type = value.get("type")
            entity_id = value.get("id")
            if isinstance(entity_type, str) and isinstance(entity_id, str):
                output.append(_EntityPointer(path=path, type=entity_type, id=entity_id))

            for key, nested in value.items():
                nested_path = f"{path}.{key}" if path else str(key)
                self._collect_entity_pointers(nested, path=nested_path, output=output)

        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for idx, nested in enumerate(value):
                nested_path = f"{path}[{idx}]" if path else f"[{idx}]"
                self._collect_entity_pointers(nested, path=nested_path, output=output)

    async def build_notification_dto(
        self, notification: Notification
    ) -> NotificationDTO:
        """Build NotificationDTO from Notification entity and its metadata."""
        metadata_raw = getattr(notification, "metadata_payload", None)

        metadata_map: dict[str, Any]
        if isinstance(metadata_raw, dict):
            metadata_map = metadata_raw
        else:
            metadata_map = {}

        entities = await self.resolve_entities_from_metadata([metadata_map]) if metadata_map else {}

        return NotificationDTO.from_notification(
            notification=notification,
            metadata_map=metadata_map,
            resolved_entities=entities,
        )
