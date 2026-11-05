"""`TopicRepository` —— 状态在这里，方法全是一句转发。

`_session` 由 `__init__` 建立；每个方法的实现都在同级子模块里同名的那个函数中
（`reads` / `writes` / `replied` / `unread`），所以行为、方法和 patch 点和拆文件
之前一样。包的说明在 `app/domain/topic/repositories/__init__.py`。

一句转发的方法不拿决定 —— 决定（哪条读点、哪个判据、怎么写）在每个子模块里各写
一遍，这里再抄一份就会漂移。
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from datetime import datetime

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domain.topic.models import NotifyLevel, Topic, TopicKind
from app.domain.topic.repositories.vocabulary import (
    ListedRoom,
    SortOrder,
    TopicSortField,
    Unread,
)

from . import reads, replied, unread, writes


class TopicRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    # --- 写 --------------------------------------------------------------------

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        number: int,
        environment: dict,
        title: str,
        description: str | None = None,
        members_only: bool = False,
        parent_id: uuid.UUID | None = None,
        kind: TopicKind = TopicKind.topic,
        created_by: str | None = None,
        upgraded_from_block_id: uuid.UUID | None = None,
    ) -> Topic:
        return await writes.add(
            self,
            project_id=project_id,
            number=number,
            environment=environment,
            title=title,
            description=description,
            members_only=members_only,
            parent_id=parent_id,
            kind=kind,
            created_by=created_by,
            upgraded_from_block_id=upgraded_from_block_id,
        )

    async def lock(self, topic_id: uuid.UUID) -> Topic | None:
        return await writes.lock(self, topic_id=topic_id)

    async def lock_all_in_project(self, project_id: uuid.UUID) -> list[Topic]:
        return await writes.lock_all_in_project(self, project_id=project_id)

    async def get_or_create_private(
        self,
        *,
        project_id: uuid.UUID,
        owner: str,
        peer: str,
        title: str,
        environment: dict,
    ) -> Topic:
        return await writes.get_or_create_private(
            self,
            project_id=project_id,
            owner=owner,
            peer=peer,
            title=title,
            environment=environment,
        )

    # --- 读（整行 / 投影 / 计数） -----------------------------------------------

    async def get(self, topic_id: uuid.UUID) -> Topic | None:
        return await reads.get(self, topic_id=topic_id)

    async def room_of_conversation(
        self, conversation_id: uuid.UUID, *, member: str | None = None
    ) -> Topic | None:
        return await reads.room_of_conversation(
            self, conversation_id=conversation_id, member=member
        )

    async def get_many(self, topic_ids: set[uuid.UUID]) -> list[Topic]:
        return await reads.get_many(self, topic_ids=topic_ids)

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        sort: TopicSortField | None = None,
        order: SortOrder = "asc",
        active_since: datetime | None = None,
        only_seen_by: str | None = None,
    ) -> list[Topic]:
        return await reads.list_for_project(
            self,
            project_id=project_id,
            sort=sort,
            order=order,
            active_since=active_since,
            only_seen_by=only_seen_by,
        )

    async def ids_for_project(
        self,
        project_id: uuid.UUID,
        *,
        only_seen_by: str | None = None,
        unarchived: bool = False,
    ) -> list[uuid.UUID]:
        return await reads.ids_for_project(
            self,
            project_id=project_id,
            only_seen_by=only_seen_by,
            unarchived=unarchived,
        )

    async def visible_unarchived_ids_for_projects(
        self, project_ids: list[uuid.UUID], *, viewer: str
    ) -> list[uuid.UUID]:
        return await reads.visible_unarchived_ids_for_projects(
            self, project_ids=project_ids, viewer=viewer
        )

    async def list_by_ids(self, topic_ids: Collection[uuid.UUID]) -> list[Topic]:
        return await reads.list_by_ids(self, topic_ids=topic_ids)

    async def listed_by_ids(self, topic_ids: Collection[uuid.UUID]) -> list[ListedRoom]:
        return await reads.listed_by_ids(self, topic_ids=topic_ids)

    def _project_topics_stmt(
        self,
        project_ids: list[uuid.UUID],
        *,
        sort: TopicSortField | None,
        order: SortOrder,
        active_since: datetime | None,
        seen: ColumnElement[bool] | None = None,
    ) -> Select[tuple[Topic]]:
        return reads._project_topics_stmt(
            self,
            project_ids=project_ids,
            sort=sort,
            order=order,
            active_since=active_since,
            seen=seen,
        )

    async def names_in_projects(
        self, project_ids: list[uuid.UUID], *, viewer: str
    ) -> list[Topic]:
        return await reads.names_in_projects(
            self, project_ids=project_ids, viewer=viewer
        )

    async def list_for_project_with_activity(
        self,
        project_id: uuid.UUID,
        *,
        viewer: str | None,
        sort: TopicSortField | None = None,
        order: SortOrder = "asc",
        active_since: datetime | None = None,
    ) -> list[tuple[Topic, datetime]]:
        return await reads.list_for_project_with_activity(
            self,
            project_id=project_id,
            viewer=viewer,
            sort=sort,
            order=order,
            active_since=active_since,
        )

    async def last_activity_for_topics(
        self, topic_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        return await reads.last_activity_for_topics(self, topic_ids=topic_ids)

    async def list_children(self, parent_id: uuid.UUID) -> list[Topic]:
        return await reads.list_children(self, parent_id=parent_id)

    async def archival_for_project(
        self, project_id: uuid.UUID
    ) -> dict[uuid.UUID, datetime | None]:
        return await reads.archival_for_project(self, project_id=project_id)

    async def count_for_project(
        self, project_id: uuid.UUID, *, viewer: str | None
    ) -> int:
        return await reads.count_for_project(self, project_id=project_id, viewer=viewer)

    async def has_open_tasks(self, room_id: uuid.UUID) -> bool:
        return await reads.has_open_tasks(self, room_id=room_id)

    async def task_conversations_in(self, room_ids: list[uuid.UUID]) -> list[uuid.UUID]:
        return await reads.task_conversations_in(self, room_ids=room_ids)

    # ---- 有人回复了我、我还没处理（侧栏那盏黄灯） -----------------------

    async def replied_to_me(
        self, room_ids: Collection[uuid.UUID], user_handle: str
    ) -> set[uuid.UUID]:
        return await replied.replied_to_me(
            self, room_ids=room_ids, user_handle=user_handle
        )

    # ---- 话题级未读 (Feishu-style badges) -------------------------------

    async def unread_counts(
        self, project_id: uuid.UUID, user_handle: str
    ) -> dict[uuid.UUID, Unread]:
        return await unread.unread_counts(
            self, project_id=project_id, user_handle=user_handle
        )

    async def _levels(
        self, topic_ids: list[uuid.UUID], user_handle: str
    ) -> dict[uuid.UUID, NotifyLevel]:
        return await unread._levels(self, topic_ids=topic_ids, user_handle=user_handle)

    async def muted_among(self, topic_id: uuid.UUID, handles: list[str]) -> set[str]:
        return await unread.muted_among(self, topic_id=topic_id, handles=handles)

    async def private_unread_counts(
        self,
        project_id: uuid.UUID,
        user_handle: str,
        *,
        seats: dict[str, str],
        default_agent_instance_id: uuid.UUID | None,
    ) -> dict[str, int]:
        return await unread.private_unread_counts(
            self,
            project_id=project_id,
            user_handle=user_handle,
            seats=seats,
            default_agent_instance_id=default_agent_instance_id,
        )

    async def notify_levels(
        self, project_id: uuid.UUID, user_handle: str
    ) -> dict[uuid.UUID, tuple[NotifyLevel, datetime | None]]:
        return await unread.notify_levels(
            self, project_id=project_id, user_handle=user_handle
        )

    async def set_notify_level(
        self,
        topic_id: uuid.UUID,
        user_handle: str,
        level: NotifyLevel,
        muted_until: datetime | None = None,
    ) -> None:
        return await unread.set_notify_level(
            self,
            topic_id=topic_id,
            user_handle=user_handle,
            level=level,
            muted_until=muted_until,
        )

    async def mark_read_many(
        self, topic_ids: list[uuid.UUID], user_handle: str
    ) -> None:
        return await unread.mark_read_many(
            self, topic_ids=topic_ids, user_handle=user_handle
        )

    async def mark_read(self, topic_id: uuid.UUID, user_handle: str) -> None:
        return await unread.mark_read(self, topic_id=topic_id, user_handle=user_handle)
