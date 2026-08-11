"""Accept card data access."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.review.models import AcceptApproval, AcceptCard, AcceptStatus
from app.domain.topic.models import Topic, TopicStatus


class AcceptCardRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        topic_id: uuid.UUID,
        reviewer_handle: str,
        routing_reason: str = "",
        status: AcceptStatus = AcceptStatus.pending,
    ) -> AcceptCard:
        card = AcceptCard(
            topic_id=topic_id,
            reviewer_handle=reviewer_handle,
            routing_reason=routing_reason,
            status=status,
        )
        self._session.add(card)
        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def list_approver_handles(self, card_id: uuid.UUID) -> list[str]:
        stmt = (
            select(AcceptApproval.approver_handle)
            .where(AcceptApproval.card_id == card_id)
            .order_by(AcceptApproval.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def add_approval(self, card_id: uuid.UUID, approver_handle: str) -> None:
        """Record a vote; idempotent — (card, approver) is unique by design."""
        if approver_handle in await self.list_approver_handles(card_id):
            return
        self._session.add(
            AcceptApproval(card_id=card_id, approver_handle=approver_handle)
        )
        await self._session.flush()

    async def get(self, card_id: uuid.UUID) -> AcceptCard | None:
        return await self._session.get(AcceptCard, card_id)

    async def list_for_topic(self, topic_id: uuid.UUID) -> list[AcceptCard]:
        stmt = (
            select(AcceptCard)
            .where(AcceptCard.topic_id == topic_id)
            .order_by(AcceptCard.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_pr_open_on_active_topics(self) -> list[AcceptCard]:
        """两阶段采纳 (PR迭代式): every card the PR/deploy poller may advance.

        孤儿卡修复 (2026-08-10): the topic's status is part of the predicate, not
        just the card's. Without the join this returned cards on ARCHIVED topics
        too, and the poller kept driving them every 60s with the approver's
        GitHub token — pushing branches and merging PRs for work nobody is
        tracking any more. `TopicService._archive_one` now closes those cards at
        archive time; this join is the second lock, covering rows that predate
        the fix or arrive by some future archive path.
        """
        stmt = (
            select(AcceptCard)
            .join(Topic, Topic.id == AcceptCard.topic_id)
            .where(
                AcceptCard.status == AcceptStatus.pr_open,
                Topic.status != TopicStatus.archived,
            )
        )
        return list((await self._session.scalars(stmt)).all())
