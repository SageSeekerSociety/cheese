"""Topic membership data access."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.topic.models import TopicMembership, TopicRole


class TopicMembershipRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self, *, topic_id: uuid.UUID, member_handle: str, role: TopicRole
    ) -> TopicMembership:
        member = TopicMembership(
            topic_id=topic_id, member_handle=member_handle, role=role
        )
        self._session.add(member)
        await self._session.flush()
        await self._session.refresh(member)
        return member

    async def get(
        self, *, topic_id: uuid.UUID, member_handle: str
    ) -> TopicMembership | None:
        stmt = select(TopicMembership).where(
            TopicMembership.topic_id == topic_id,
            TopicMembership.member_handle == member_handle,
        )
        return await self._session.scalar(stmt)

    async def list_for_topic(self, topic_id: uuid.UUID) -> list[TopicMembership]:
        stmt = (
            select(TopicMembership)
            .where(TopicMembership.topic_id == topic_id)
            .order_by(TopicMembership.created_at.asc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def topic_ids_for_member(
        self,
        topic_ids: list[uuid.UUID],
        member_handle: str,
        *,
        roles: frozenset[TopicRole] | None = None,
    ) -> set[uuid.UUID]:
        """Which of these topics this handle sits in the roster of, in ONE query.

        By topic-SET rather than per topic because the caller is the sidebar's
        list endpoint: it asks the same question about every topic in a project
        at once, and asking it one row at a time is the N+1 that makes a
        hundred-topic project unopenable.
        """
        if not topic_ids:
            return set()
        stmt = select(TopicMembership.topic_id).where(
            TopicMembership.topic_id.in_(topic_ids),
            TopicMembership.member_handle == member_handle,
        )
        if roles is not None:
            stmt = stmt.where(TopicMembership.role.in_(roles))
        return set((await self._session.scalars(stmt)).all())

    async def count_for_topic(self, topic_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(TopicMembership)
            .where(TopicMembership.topic_id == topic_id)
        )
        return int((await self._session.scalar(stmt)) or 0)

    async def count_owners(self, topic_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(TopicMembership)
            .where(
                TopicMembership.topic_id == topic_id,
                TopicMembership.role == TopicRole.owner,
            )
        )
        return int((await self._session.scalar(stmt)) or 0)

    async def owners_by_topic(
        self, topic_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[str]]:
        """Who owns each of these topics, in ONE query.

        By topic-SET for the same reason as ``topic_ids_for_member``: the caller
        is a project-level exit (退项目 / 被移出项目) asking one question about
        every room the leaver holds a seat in — "would revoking this seat leave
        the room with nobody in charge". Asking it per topic is the N+1 that
        makes a hundred-topic project expensive, and the answer comes back
        grouped here.

        Handles, not a count: the asker has to tell "he is the only one left"
        apart from "others are still there", and a number cannot say which.
        """
        if not topic_ids:
            return {}
        stmt = select(TopicMembership.topic_id, TopicMembership.member_handle).where(
            TopicMembership.topic_id.in_(topic_ids),
            TopicMembership.role == TopicRole.owner,
        )
        owners: dict[uuid.UUID, list[str]] = {}
        for topic_id, member_handle in (await self._session.execute(stmt)).all():
            owners.setdefault(topic_id, []).append(member_handle)
        return owners

    async def update_role(
        self, member: TopicMembership, *, role: TopicRole
    ) -> TopicMembership:
        member.role = role
        await self._session.flush()
        await self._session.refresh(member)
        return member

    async def delete(self, member: TopicMembership) -> None:
        await self._session.delete(member)
        await self._session.flush()
