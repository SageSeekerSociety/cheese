"""收件箱的业务逻辑 —— 一张表，两侧读者。

`NotificationQueryService` 是知是那一侧的站内信（按账号 id 查，游标翻页）。
`ProjectNotificationService` 是项目收件箱（按项目 + 名册上的名字查，角标、等你决
定、话题相关性）。两个读写的是同一张 `notification`。
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.domain.agent.runtime import get_broker
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.identity.handles import looks_like_agent_handle
from app.domain.membership.services import MemberService
from app.domain.notification.dto import NotificationDTO, ResolvedEntityInfoDTO
from app.domain.notification.entity_resolvers import EntityInfoResolver
from app.domain.notification.models import (
    Notification,
    NotificationLevel,
    NotificationType,
)
from app.domain.notification.repositories import NotificationRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.topic_membership.services import TopicMemberService
from app.domain.user.services import user_by_handle

_VALID_FEEDBACK = {"up", "down"}


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
        return await self._repo.get_by_id_for_user(
            user_id=user_id, notification_id=notification_id
        )

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

        notifications = await self._repo.find_all_by_ids_for_user(
            user_id=user_id, ids=ids
        )
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

    async def delete_notification_for_current_user(
        self, user_id: int, notification_id: int
    ) -> bool:
        return await self._repo.soft_delete_for_user(
            user_id=user_id, notification_id=notification_id
        )

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

        elif isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
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

        entities = (
            await self.resolve_entities_from_metadata([metadata_map])
            if metadata_map
            else {}
        )

        return NotificationDTO.from_notification(
            notification=notification,
            metadata_map=metadata_map,
            resolved_entities=entities,
        )


class ProjectNotificationService:
    """项目收件箱 —— 平台报告自己的那一侧（spec §8.5/8.6）。

    以前这是 `alert/` 那个包，一张自己的 `alerts` 表。两张表并成一张之后它写的是
    `notification`，和人对人的那些同住一张表、同一个收件箱。
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = NotificationRepository(session)
        self._projects = ProjectRepository(session)

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        level: NotificationLevel,
        kind: NotificationType,
        title: str,
        body: str = "",
        target_handle: str | None = None,
        topic_id: uuid.UUID | None = None,
        payload: dict | None = None,
    ) -> list[Notification]:
        """写进收件箱，一个收件人一行。

        `target_handle` 为空是**广播**：在这里就展开成一人一行，而不是留一行
        `target_handle IS NULL` 让每一处读都自己想起来「还有谁都看得见的那一档」。
        """
        if await self._projects.get(project_id) is None:
            raise NotFoundError("Project not found")
        recipients = (
            [target_handle]
            if target_handle is not None
            else await self._broadcast_roster(project_id, topic_id)
        )
        if not recipients:
            # 名册上一个人都不剩（只坐着 agent，或者项目既没成员也没主人）。并表
            # 之前广播是一行 `target_handle IS NULL`，谁读都看得见，丢不掉；展开
            # 成一人一行之后「展开成零行」就是把整条通知扔了，而路由照样回 200、
            # `cheese notify` 把返回值整个丢掉 —— 发的人和收的人都不会知道。一条
            # 到不了任何人的通知不是成功。
            raise ValidationError(
                "这条通知没有收件人：这个房间的名册上只有 agent（或者这个项目还"
                "没有成员和主人）。点名一个人再发。"
            )
        rows: list[Notification] = []
        for handle in recipients:
            user = await user_by_handle(self._session, handle)
            rows.append(
                await self._repo.add(
                    project_id=project_id,
                    recipient_handle=handle,
                    receiver_id=user.id if user is not None else None,
                    level=level,
                    type_=kind,
                    title=title,
                    body=body,
                    topic_id=topic_id,
                    payload=payload,
                )
            )
        return rows

    async def _broadcast_roster(
        self, project_id: uuid.UUID, topic_id: uuid.UUID | None
    ) -> list[str]:
        """一条广播到得了谁手上 —— 当时房间里的人，没说房间就是整个项目的名册。

        agent 不在里面：它在自己房间的时间线上读到这件事，往它的收件箱里塞一行写
        的是一条谁都不会打开的记录（`identity/arrival.py`）。
        """
        if topic_id is not None:
            service = TopicMemberService(self._session)
            members, _ = await service.list_for_topic(topic_id)
            handles = [m.member_handle for m in members]
        else:
            members, _ = await MemberService(self._session).list_for_project(project_id)
            handles = [m.user_handle for m in members]
            project = await self._projects.get(project_id)
            if project is not None and project.owner_handle:
                handles.append(project.owner_handle)
        return [
            handle
            for handle in dict.fromkeys(handles)
            if not looks_like_agent_handle(handle)
        ]

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        target_handle: str | None = None,
        unread_only: bool = False,
    ) -> tuple[list[Notification], int]:
        items = await self._repo.list_for_project(
            project_id, recipient_handle=target_handle, unread_only=unread_only
        )
        return items, len(items)

    async def inbox(
        self,
        project_id: uuid.UUID,
        *,
        target_handle: str | None = None,
    ) -> tuple[list[Notification], int]:
        items = await self._repo.list_inbox(project_id, recipient_handle=target_handle)
        return items, len(items)

    async def unread_count(
        self, project_id: uuid.UUID, *, target_handle: str | None = None
    ) -> int:
        return await self._repo.unread_count_in_project(
            project_id, recipient_handle=target_handle
        )

    async def mention_topic_ids(
        self, topic_ids: list[uuid.UUID], target_handle: str
    ) -> dict[uuid.UUID, bool]:
        """{topic_id: 这里还有没有 @ 他的未读}。

        话题列表要的「被 @ 过」和「@我的未读」是同一条索引查询顺带得出的 —— 一个
        @ 落地时就在收件箱里写了一行，不必翻消息正文。
        """
        return await self._repo.mention_topic_ids(topic_ids, target_handle)

    async def mark_all_read(
        self, project_id: uuid.UUID, *, target_handle: str | None = None
    ) -> int:
        return await self._repo.mark_all_read_in_project(
            project_id, recipient_handle=target_handle
        )

    async def get_or_404(self, notification_id: int) -> Notification:
        """这个 id 指的那条项目通知。

        不属于项目收件箱的行（人对人的那些，以及账本之前写下的没有收件人的旧
        行）在这条路上是**不存在**的，不是「存在但不该看」：`/alerts/{id}/...`
        那三个动作把授权落在这一行的收件人上，而没有收件人的行让那句话失效 ——
        任何验证过的调用者都会被当成它的主人。并表把两种行放进了同一张表，所以
        这道判断必须在这里，不能留在「反正查不到别人的」。
        """
        row = await self._repo.get(notification_id)
        if row is None or row.project_id is None or row.recipient_handle is None:
            raise NotFoundError("Notification not found")
        return row

    async def mark_read(self, notification_id: int) -> Notification:
        row = await self.get_or_404(notification_id)
        row.read = True
        return await self._repo.save(row)

    async def resolve(
        self, notification_id: int, *, chosen: str, decided_by: str
    ) -> Notification:
        """拍板 (spec G2)：把选的那一项记在这条上，并把决定丢回房间，芝士下一轮
        读得到。"""
        row = await self.get_or_404(notification_id)
        if row.type != NotificationType.DECISION_REQUEST:
            raise ValidationError("只有决策请求可以拍板")
        # 幂等：一件事只拍一次板，重复拍不会往房间里再丢一条【决策】。
        if row.resolved_at is not None:
            return row
        payload = dict(row.metadata_payload or {})
        options = payload.get("options") or []
        if options and chosen not in options:
            raise ValidationError("所选项不在候选项中")
        row.resolved_at = datetime.now(UTC)
        row.read = True
        payload["resolved_choice"] = chosen
        row.metadata_payload = payload
        block = None
        if row.topic_id is not None and row.project_id is not None:
            block = await BlockRepository(self._session).add(
                project_id=row.project_id,
                topic_id=row.topic_id,
                author=decided_by,
                author_type=AuthorType.participant,
                content=f"【决策】关于「{row.title}」：选择「{chosen}」。",
                kind=BlockKind.message,
            )
        saved = await self._repo.save(row)
        if block is not None:
            block_payload = BlockOut.model_validate(block).model_dump(mode="json")
            await self._session.commit()
            await get_broker().publish(
                str(block.topic_id), {"type": "event_block", "block": block_payload}
            )
        return saved

    async def set_feedback(self, notification_id: int, feedback: str) -> Notification:
        if feedback not in _VALID_FEEDBACK:
            raise ValidationError("feedback must be 'up' or 'down'")
        row = await self.get_or_404(notification_id)
        row.feedback = feedback
        return await self._repo.save(row)
