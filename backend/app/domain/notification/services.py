"""Notification business logic."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.domain.notification.models import Notification, NotifKind, NotifLevel
from app.domain.notification.repositories import NotificationRepository
from app.domain.project.repositories import ProjectRepository

_VALID_FEEDBACK = {"up", "down"}


class NotificationService:
    def __init__(self, session: AsyncSession):
        self._repo = NotificationRepository(session)
        self._projects = ProjectRepository(session)

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        level: NotifLevel,
        kind: NotifKind,
        title: str,
        body: str = "",
        target_handle: str | None = None,
        topic_id: uuid.UUID | None = None,
        payload: dict | None = None,
    ) -> Notification:
        if await self._projects.get(project_id) is None:
            raise NotFoundError("Project not found")
        return await self._repo.add(
            project_id=project_id,
            level=level,
            kind=kind,
            title=title,
            body=body,
            target_handle=target_handle,
            topic_id=topic_id,
            payload=payload,
        )

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        target_handle: str | None = None,
        unread_only: bool = False,
    ) -> tuple[list[Notification], int]:
        items = await self._repo.list_for_project(
            project_id, target_handle=target_handle, unread_only=unread_only
        )
        return items, len(items)

    async def inbox(
        self,
        project_id: uuid.UUID,
        *,
        target_handle: str | None = None,
    ) -> tuple[list[Notification], int]:
        items = await self._repo.list_inbox(project_id, target_handle=target_handle)
        return items, len(items)

    async def get_or_404(self, notification_id: uuid.UUID) -> Notification:
        notification = await self._repo.get(notification_id)
        if notification is None:
            raise NotFoundError("Notification not found")
        return notification

    async def mark_read(self, notification_id: uuid.UUID) -> Notification:
        notification = await self.get_or_404(notification_id)
        notification.read_at = datetime.now(UTC)
        return await self._repo.save(notification)

    async def set_feedback(
        self, notification_id: uuid.UUID, feedback: str
    ) -> Notification:
        if feedback not in _VALID_FEEDBACK:
            raise ValidationError("feedback must be 'up' or 'down'")
        notification = await self.get_or_404(notification_id)
        notification.feedback = feedback
        return await self._repo.save(notification)
