"""Topic membership data access."""

import uuid

from sqlalchemy import delete, func, select
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

    async def roles_for_member(
        self, topic_ids: list[uuid.UUID], member_handle: str
    ) -> dict[uuid.UUID, TopicRole]:
        """The role this handle holds in each of these topics, in ONE query.
        Topics the handle is not in simply do not appear in the result."""
        if not topic_ids:
            return {}
        stmt = select(TopicMembership.topic_id, TopicMembership.role).where(
            TopicMembership.topic_id.in_(topic_ids),
            TopicMembership.member_handle == member_handle,
        )
        rows = (await self._session.execute(stmt)).all()
        return {topic_id: role for topic_id, role in rows}

    async def count_for_topic(self, topic_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(TopicMembership)
            .where(TopicMembership.topic_id == topic_id)
        )
        return int((await self._session.scalar(stmt)) or 0)

    async def delete(self, member: TopicMembership) -> None:
        await self._session.delete(member)
        await self._session.flush()

    async def delete_for_member(
        self, *, topic_ids: list[uuid.UUID], member_handle: str
    ) -> list[uuid.UUID]:
        """把这个人在这批话题里的席位一次删掉，返回**真的删掉**的那些 topic id。

        退项目 / 被移出项目一次要清掉他在整个项目里的席位，早先是一条一条
        ``get`` 再 ``delete``（一个项目多少间房就多少次往返）。一条 ``DELETE ...
        IN (...)`` 是同一件事，但**答案更准**：以前那条路只能拿「查到的席位」当
        「删掉的席位」交出去，中途被别人删掉的那几条会让调用方以为它撤了其实没撤；
        ``RETURNING topic_id`` 交出的就是数据库实际删掉的行。

        空列表（这批话题里他本来就没席位）就等于「什么都没写」，调用方靠它把「确实
        撤销了」和「压根没动」分开 —— 退项目最后那句「你不是成员」只在前者为空时
        才说得出口。

        用 ``execute(...).all()`` 而不是 ORM 的实体删除：这里只要 id，不需要把行
        取回对象再标记删除，省一趟数据库。
        """
        if not topic_ids:
            return []
        stmt = (
            delete(TopicMembership)
            .where(
                TopicMembership.topic_id.in_(topic_ids),
                TopicMembership.member_handle == member_handle,
            )
            .returning(TopicMembership.topic_id)
        )
        return [row[0] for row in (await self._session.execute(stmt)).all()]
