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
from app.core.sentences import say
from app.domain.agent.runtime import get_broker
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.conversation.services import room_of
from app.domain.identity.handles import looks_like_agent_handle
from app.domain.notification.dto import NotificationDTO, ResolvedEntityInfoDTO
from app.domain.notification.entity_resolvers import EntityInfoResolver
from app.domain.notification.models import (
    Notification,
    NotificationLevel,
    NotificationType,
)
from app.domain.notification.preferences import in_app_allowed
from app.domain.notification.preferences_models import PreferencesRepository
from app.domain.notification.repositories import NotificationRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.topic_membership.services import TopicMemberService
from app.domain.user.services import user_by_handle


@dataclass(slots=True)
class _EntityPointer:
    path: str
    type: str
    id: str


def _metadata_map_of(notification: Notification) -> dict[str, Any]:
    """这条通知的元数据，非字典（None、字符串、旧行）一律当空。"""
    metadata_raw = getattr(notification, "metadata_payload", None)
    return metadata_raw if isinstance(metadata_raw, dict) else {}


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
            self._pointers_in(metadata, output=pointers)

        if not pointers:
            return {}

        resolved_by_type = await self._resolve_pointers_by_type(pointers)
        return self._flatten_pointers(pointers, resolved_by_type)

    async def resolve_entities_for_notifications(
        self, notifications: Sequence[Notification]
    ) -> list[dict[str, ResolvedEntityInfoDTO | None]]:
        """一整页通知各自的实体解析结果，整页每类实体只查一次。

        返回的每一项和 ``resolve_entities_from_metadata([这一条自己的 metadata])``
        同形。逐条各解析一次是一屏 N 倍往返 —— 而且每条只剩一个 id 可查，连解析器
        自己的批量查询也退化成单条（``ProjectEntityResolver`` 里更是逐 id
        ``get_or_404``）。这里把整页的 id 汇总起来查一次，再摊回每一条。

        摊回时**每条各摊各的**：``resolve_entities_from_metadata`` 返回的是按 path
        拍平的字典，两条通知若有同名顶层键（都写着 ``actor``），拍进同一张表就会
        互相覆盖。
        """
        pointers_per_notification: list[list[_EntityPointer]] = []
        every_pointer: list[_EntityPointer] = []
        for notification in notifications:
            pointers: list[_EntityPointer] = []
            self._pointers_in(_metadata_map_of(notification), output=pointers)
            pointers_per_notification.append(pointers)
            every_pointer.extend(pointers)

        resolved_by_type = await self._resolve_pointers_by_type(every_pointer)

        return [
            self._flatten_pointers(pointers, resolved_by_type)
            for pointers in pointers_per_notification
        ]

    def _pointers_in(
        self, metadata: Mapping[str, Any], *, output: list[_EntityPointer]
    ) -> None:
        for key, value in metadata.items():
            self._collect_entity_pointers(value, path=str(key), output=output)

    async def _resolve_pointers_by_type(
        self, pointers: Sequence[_EntityPointer]
    ) -> dict[str, dict[str, ResolvedEntityInfoDTO | None]]:
        """每一类实体查一次，问的是这批指针里出现过的全部 id。"""
        ids_by_type: dict[str, set[str]] = {}
        for pointer in pointers:
            ids_by_type.setdefault(pointer.type, set()).add(pointer.id)

        resolved_by_type: dict[str, dict[str, ResolvedEntityInfoDTO | None]] = {}
        for entity_type, ids in ids_by_type.items():
            resolver = self._resolver_map.get(entity_type)
            if resolver is None:
                continue
            resolved_by_type[entity_type] = await resolver.resolve(sorted(ids))
        return resolved_by_type

    def _flatten_pointers(
        self,
        pointers: Sequence[_EntityPointer],
        resolved_by_type: Mapping[str, Mapping[str, ResolvedEntityInfoDTO | None]],
    ) -> dict[str, ResolvedEntityInfoDTO | None]:
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
        self,
        notification: Notification,
        *,
        resolved_entities: dict[str, ResolvedEntityInfoDTO | None] | None = None,
    ) -> NotificationDTO:
        """Build NotificationDTO from Notification entity and its metadata.

        ``resolved_entities`` 是给**已经整页解析过**的调用方用的出口：传进来就
        不再解析一次。少了这个出口，整页批量解析反而会每条再解析一遍，N+1 照旧
        —— 那个函数是幂等的，但不是免费的。
        """
        metadata_map = _metadata_map_of(notification)

        if resolved_entities is None:
            entities = (
                await self.resolve_entities_from_metadata([metadata_map])
                if metadata_map
                else {}
            )
        else:
            entities = resolved_entities

        return NotificationDTO.from_notification(
            notification=notification,
            metadata_map=metadata_map,
            resolved_entities=entities,
        )

    async def build_notification_dtos(
        self, notifications: Sequence[Notification]
    ) -> list[NotificationDTO]:
        """整页 DTO，实体解析每类只发一次查询。列表端点走这里。"""
        resolved_pages = await self.resolve_entities_for_notifications(notifications)
        return [
            await self.build_notification_dto(
                notification, resolved_entities=resolved_entities
            )
            for notification, resolved_entities in zip(
                notifications, resolved_pages, strict=True
            )
        ]


def asks_for_decision(notification: Notification) -> bool:
    """这一条是不是要人拍板的决策请求（另一种进「待办」的是读过就了结的变更提醒）。"""
    return notification.type == NotificationType.DECISION_REQUEST.value


class ProjectNotificationService:
    """项目收件箱 —— 平台报告自己的那一侧（spec §8.5/8.6）。

    以前这是 `alert/` 那个包，一张自己的 `alerts` 表。两张表并成一张之后它写的是
    `notification`，和人对人的那些同住一张表、同一个收件箱。
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = NotificationRepository(session)
        self._projects = ProjectRepository(session)
        self._prefs = PreferencesRepository(session)

    async def still_open(
        self, project_ids: list[uuid.UUID], *, recipient_handle: str
    ) -> list[Notification]:
        """这几个项目里写给他、还没了结的决策请求和变更提醒（「待办」那一份清单读它）。"""
        return await self._repo.still_open(
            project_ids, recipient_handle=recipient_handle
        )

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        level: NotificationLevel,
        kind: NotificationType,
        title: str,
        body: str = "",
        target_handle: str | None = None,
        conversation_id: uuid.UUID | None = None,
        payload: dict | None = None,
    ) -> list[Notification]:
        """写进收件箱，一个收件人一行。

        `conversation_id` 是这条通知关于的那条对话（一个频道自己那条线、它的一条
        任务、或一条支线）；广播到的是那条对话所在频道的人。

        `target_handle` 为空是**广播**：在这里就展开成一人一行，而不是留一行
        `target_handle IS NULL` 让每一处读都自己想起来「还有谁都看得见的那一档」。
        """
        if await self._projects.get(project_id) is None:
            raise NotFoundError("Project not found")
        recipients = (
            [target_handle]
            if target_handle is not None
            else await self._broadcast_roster(project_id, conversation_id)
        )
        if not recipients:
            # 名册上一个人都不剩（只坐着 agent，或者项目既没成员也没主人）。并表
            # 之前广播是一行 `target_handle IS NULL`，谁读都看得见，丢不掉；展开
            # 成一人一行之后「展开成零行」就是把整条通知扔了，而路由照样回 200、
            # `cheese_notify` 把返回值整个丢掉 —— 发的人和收的人都不会知道。一条
            # 到不了任何人的通知不是成功。
            raise ValidationError(say("notificationNoRecipients"))
        rows: list[Notification] = []
        for handle in recipients:
            user = await user_by_handle(self._session, handle)
            # 站内也是收件人的选择：这一类他关掉了就不写进他的收件箱。没有账号行
            # 的收件人（`receiver_id` 为空）读不到偏好，按默认（进站内）。
            if user is not None:
                pref = await self._prefs.for_user(user.id)
                if not in_app_allowed(pref, kind):
                    continue
            rows.append(
                await self._repo.add(
                    project_id=project_id,
                    recipient_handle=handle,
                    receiver_id=user.id if user is not None else None,
                    level=level,
                    type_=kind,
                    title=title,
                    body=body,
                    conversation_id=conversation_id,
                    payload=payload,
                )
            )
        return rows

    async def _broadcast_roster(
        self, project_id: uuid.UUID, conversation_id: uuid.UUID | None
    ) -> list[str]:
        """一条广播到得了谁手上 —— 这条对话所在频道里的人，不说对话就是整个项目
        的人。

        名册和机器是频道的（`Place`）：一条在任务或支线里的通知，到得了的是那个
        频道的人，不是那条任务自己的（它没有自己的名册）。

        agent 不在里面：它在自己房间的时间线上读到这件事，往它的收件箱里塞一行写
        的是一条谁都不会打开的记录（`identity/arrival.py`）。
        """
        service = TopicMemberService(self._session)
        if conversation_id is not None:
            handles = await service.people_of(
                await room_of(self._session, conversation_id)
            )
        else:
            handles = await service.project_people(project_id)
        return [
            handle
            for handle in dict.fromkeys(handles)
            if not looks_like_agent_handle(handle)
        ]

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        target_handle: str,
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
        target_handle: str,
    ) -> tuple[list[Notification], int]:
        items = await self._repo.list_inbox(project_id, recipient_handle=target_handle)
        return items, len(items)

    async def unread_count(self, project_id: uuid.UUID, *, target_handle: str) -> int:
        return await self._repo.unread_count_in_project(
            project_id, recipient_handle=target_handle
        )

    async def decision_topic_ids(
        self, topic_ids: list[uuid.UUID], target_handle: str
    ) -> dict[uuid.UUID, bool]:
        """{频道: 这里向他要的决策还有没有没拍板的} —— 频道自己的线和它的任务、
        支线一起算。"""
        return await self._repo.decision_topic_ids(topic_ids, target_handle)

    async def mark_all_read(self, project_id: uuid.UUID, *, target_handle: str) -> int:
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
        """拍板 (spec G2)：把选的那一项记在这条上，并把决定丢回问这件事的那条
        对话，芝士下一轮读得到。"""
        row = await self.get_or_404(notification_id)
        if row.type != NotificationType.DECISION_REQUEST:
            raise ValidationError(say("notificationOnlyDecisions"))
        # 幂等：一件事只拍一次板，重复拍不会往房间里再丢一条【决策】。
        if row.resolved_at is not None:
            return row
        payload = dict(row.metadata_payload or {})
        options = payload.get("options") or []
        if options and chosen not in options:
            raise ValidationError(say("notificationOptionNotOffered"))
        row.resolved_at = datetime.now(UTC)
        row.read = True
        payload["resolved_choice"] = chosen
        row.metadata_payload = payload
        block = None
        if row.conversation_id is not None and row.project_id is not None:
            block = await BlockRepository(self._session).add(
                project_id=row.project_id,
                conversation_id=row.conversation_id,
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
                str(block.conversation_id),
                {"type": "event_block", "block": block_payload},
            )
        return saved
