"""Topic data access."""

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import (
    ColumnElement,
    SQLColumnExpression,
    UnaryExpression,
)

from app.domain.block.models import Block, BlockKind
from app.domain.topic.models import Topic, TopicKind, TopicReadState

TopicSortField = Literal["updated_at", "title", "last_activity_at"]
SortOrder = Literal["asc", "desc"]


def _last_activity() -> ColumnElement[datetime]:
    """When something last HAPPENED in a topic — the newest block it holds.

    Derived per query instead of stored on the row, because `updated_at` cannot
    answer this: it is the topics ROW's mtime (Timestamps.onupdate), and adding
    a block writes the blocks table only, so a topic that has been talked in all
    day still reports the moment its title or session id last changed. Deriving
    it also means no backfill for the topics that already drifted, and no drift
    when blocks are deleted.

    A topic with no blocks yet falls back to its own creation — "nothing has
    happened since it was made" is the truth for a room nobody has spoken in,
    and it keeps the value non-null so sorting and filtering stay total.
    """
    newest_block = (
        select(func.max(Block.created_at))
        .where(Block.topic_id == Topic.id)
        .correlate(Topic)
        .scalar_subquery()
    )
    return func.coalesce(newest_block, Topic.created_at)


def _order_by(sort: TopicSortField | None, order: SortOrder) -> UnaryExpression:
    column: SQLColumnExpression[object]
    if sort == "title":
        column = Topic.title
    elif sort == "updated_at":
        column = Topic.updated_at
    elif sort == "last_activity_at":
        column = _last_activity()
    else:
        column = Topic.created_at
    return column.desc() if order == "desc" else column.asc()


class TopicRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        title: str,
        parent_id: uuid.UUID | None = None,
        kind: TopicKind = TopicKind.topic,
        created_by: str | None = None,
        upgraded_from_block_id: uuid.UUID | None = None,
    ) -> Topic:
        topic = Topic(
            project_id=project_id,
            title=title,
            parent_id=parent_id,
            kind=kind,
            created_by=created_by,
            upgraded_from_block_id=upgraded_from_block_id,
        )
        self._session.add(topic)
        await self._session.flush()
        await self._session.refresh(topic)
        return topic

    async def get(self, topic_id: uuid.UUID) -> Topic | None:
        return await self._session.get(Topic, topic_id)

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        sort: TopicSortField | None = None,
        order: SortOrder = "asc",
        active_since: datetime | None = None,
    ) -> list[Topic]:
        """The project's topic tree, flat.

        ``active_since`` keeps only topics whose last activity (see
        ``_last_activity``) is at or after that instant — "最近活跃的话题". It
        filters the flat list, so a kept topic's parent may be filtered out;
        callers that rebuild the tree should not combine it with the filter.
        """
        # Private chats are not part of the topic tree.
        stmt = select(Topic).where(
            Topic.project_id == project_id, Topic.is_private.is_(False)
        )
        if active_since is not None:
            stmt = stmt.where(_last_activity() >= active_since)
        stmt = stmt.order_by(_order_by(sort, order))
        return list((await self._session.scalars(stmt)).all())

    async def last_activity_for_topics(
        self, topic_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        """{topic_id: last activity} for a batch of topics, in ONE query.

        Kept off ``list_for_project`` so its return type stays ``list[Topic]``
        for the callers that only want the rows (mentions, the agent's context
        builders, the dashboard); the list endpoint joins the two by id, the
        same way it joins the unread counts.
        """
        if not topic_ids:
            return {}
        stmt = select(Topic.id, _last_activity()).where(Topic.id.in_(topic_ids))
        rows = (await self._session.execute(stmt)).all()
        return {topic_id: last for topic_id, last in rows}

    async def get_or_create_private(
        self,
        *,
        project_id: uuid.UUID,
        user_handle: str,
        peer_handle: str | None = None,
    ) -> Topic:
        """A 1:1 private conversation in this project.

        Without ``peer_handle`` this is the member's 1:1 with 芝士 (private_peer
        NULL). With ``peer_handle`` it is a person-to-person DM between the two
        humans; the unordered pair is canonicalized (owner = min, peer = max) so
        both participants get and share the same row regardless of who opens it.
        """
        if peer_handle is not None:
            owner, peer = sorted((user_handle, peer_handle))
            stmt = select(Topic).where(
                Topic.project_id == project_id,
                Topic.is_private.is_(True),
                Topic.private_owner == owner,
                Topic.private_peer == peer,
            )
        else:
            owner, peer = user_handle, None
            stmt = select(Topic).where(
                Topic.project_id == project_id,
                Topic.is_private.is_(True),
                Topic.private_owner == user_handle,
                Topic.private_peer.is_(None),
            )
        existing = (await self._session.scalars(stmt)).first()
        if existing is not None:
            return existing
        # Title is a rendering hint only; the sidebar/ChatPanel show the peer's
        # own name from the roster. Deterministic, no NL parsing (CLAUDE.md §4).
        title = f"私聊 · {owner} · {peer}" if peer else f"与芝士私聊 · {owner}"
        topic = Topic(
            project_id=project_id,
            title=title,
            kind=TopicKind.topic,
            created_by=user_handle,
            is_private=True,
            private_owner=owner,
            private_peer=peer,
        )
        self._session.add(topic)
        await self._session.flush()
        await self._session.refresh(topic)
        return topic

    async def list_children(self, parent_id: uuid.UUID) -> list[Topic]:
        stmt = (
            select(Topic).where(Topic.parent_id == parent_id).order_by(Topic.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def count_for_project(self, project_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Topic)
            .where(Topic.project_id == project_id, Topic.is_private.is_(False))
        )
        return int((await self._session.scalar(stmt)) or 0)

    async def set_session_id(self, topic: Topic, session_id: str) -> None:
        topic.session_id = session_id
        await self._session.flush()

    # ---- 话题级未读 (Feishu-style badges) -------------------------------

    async def unread_counts(
        self, project_id: uuid.UUID, user_handle: str
    ) -> dict[uuid.UUID, int]:
        """Unread message count per topic for one user, in one query.

        Unread = message blocks authored by OTHERS, created after the user's
        read cursor (no cursor = all of them). Only kind=message counts —
        doc edits / events / decisions have their own surfaces. Other
        people's private chats are excluded.
        """
        stmt = (
            select(Block.topic_id, func.count())
            .join(Topic, Topic.id == Block.topic_id)
            .outerjoin(
                TopicReadState,
                and_(
                    TopicReadState.topic_id == Block.topic_id,
                    TopicReadState.user_handle == user_handle,
                ),
            )
            .where(
                Topic.project_id == project_id,
                or_(
                    Topic.is_private.is_(False),
                    Topic.private_owner == user_handle,
                    Topic.private_peer == user_handle,
                ),
                Block.kind == BlockKind.message,
                Block.author != user_handle,
                or_(
                    TopicReadState.last_read_at.is_(None),
                    Block.created_at > TopicReadState.last_read_at,
                ),
            )
            .group_by(Block.topic_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {topic_id: int(count) for topic_id, count in rows}

    async def mark_read(self, topic_id: uuid.UUID, user_handle: str) -> None:
        """Bump the user's read cursor on a topic to now (upsert)."""
        stmt = select(TopicReadState).where(
            TopicReadState.topic_id == topic_id,
            TopicReadState.user_handle == user_handle,
        )
        state = (await self._session.scalars(stmt)).first()
        now = datetime.now(UTC)
        if state is None:
            self._session.add(
                TopicReadState(
                    topic_id=topic_id, user_handle=user_handle, last_read_at=now
                )
            )
        else:
            state.last_read_at = now
        await self._session.flush()
