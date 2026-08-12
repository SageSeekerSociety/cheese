"""Notification business logic."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.domain.agent.runtime import get_broker
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.cx_notification.models import Notification, NotifKind, NotifLevel
from app.domain.cx_notification.repositories import NotificationRepository
from app.domain.project.repositories import ProjectRepository

_VALID_FEEDBACK = {"up", "down"}


class NotificationService:
    def __init__(self, session: AsyncSession):
        self._session = session
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

    async def unread_count(
        self, project_id: uuid.UUID, *, target_handle: str | None = None
    ) -> int:
        return await self._repo.unread_count(project_id, target_handle=target_handle)

    async def mark_all_read(
        self, project_id: uuid.UUID, *, target_handle: str | None = None
    ) -> int:
        return await self._repo.mark_all_read(project_id, target_handle=target_handle)

    async def get_or_404(self, notification_id: uuid.UUID) -> Notification:
        notification = await self._repo.get(notification_id)
        if notification is None:
            raise NotFoundError("Notification not found")
        return notification

    async def mark_read(self, notification_id: uuid.UUID) -> Notification:
        notification = await self.get_or_404(notification_id)
        notification.read_at = datetime.now(UTC)
        return await self._repo.save(notification)

    async def resolve(
        self, notification_id: uuid.UUID, *, chosen: str, decided_by: str
    ) -> Notification:
        """拍板 (spec G2): record the chosen option on a decision request and drop
        the decision into the topic so 芝士 picks it up on its next turn."""
        n = await self.get_or_404(notification_id)
        if n.kind != NotifKind.decision_request:
            raise ValidationError("只有决策请求可以拍板")
        # Idempotent: a decision is resolved once. Re-resolving must not post a
        # second 【决策】block into the topic.
        if n.resolved_at is not None:
            return n
        payload = dict(n.payload or {})
        options = payload.get("options") or []
        if options and chosen not in options:
            raise ValidationError("所选项不在候选项中")
        now = datetime.now(UTC)
        n.resolved_at = now
        n.read_at = n.read_at or now
        payload["resolved_choice"] = chosen
        n.payload = payload
        block = None
        if n.topic_id is not None:
            block = await BlockRepository(self._session).add(
                project_id=n.project_id,
                topic_id=n.topic_id,
                author=decided_by,
                author_type=AuthorType.human,
                content=f"【决策】关于「{n.title}」：选择「{chosen}」。",
                kind=BlockKind.message,
            )
        saved = await self._repo.save(n)
        if block is not None:
            block_payload = BlockOut.model_validate(block).model_dump(mode="json")
            await self._session.commit()
            await get_broker().publish(
                str(block.topic_id), {"type": "event_block", "block": block_payload}
            )
        return saved

    async def set_feedback(
        self, notification_id: uuid.UUID, feedback: str
    ) -> Notification:
        if feedback not in _VALID_FEEDBACK:
            raise ValidationError("feedback must be 'up' or 'down'")
        notification = await self.get_or_404(notification_id)
        notification.feedback = feedback
        return await self._repo.save(notification)
