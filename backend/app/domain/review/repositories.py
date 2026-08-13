"""Accept card data access."""

import uuid
from datetime import datetime

from sqlalchemy import func, select
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

    async def list_stale_pending_gate(self, cutoff: datetime) -> list[AcceptCard]:
        """孤儿卡扫底 (2026-08-11): cards still waiting on a gate that started
        (or, failing that, was filed) before ``cutoff``.

        The clock is `COALESCE(gate_started_at, created_at)`, not `created_at`:
        a long worktree preparation legitimately delays the check, and rows
        written before `gate_started_at` existed have no start time at all — the
        COALESCE keeps both aging out without ever ageing a card out EARLY.

        Archived topics are excluded because `review/archive.py` already closed
        their cards; anything left there is not a deadlock (that topic can't be
        re-递卡'd anyway) and re-condemning it would just spam its history.
        """
        stmt = (
            select(AcceptCard)
            .join(Topic, Topic.id == AcceptCard.topic_id)
            .where(
                AcceptCard.status == AcceptStatus.pending_gate,
                Topic.status != TopicStatus.archived,
                func.coalesce(AcceptCard.gate_started_at, AcceptCard.created_at)
                < cutoff,
            )
            .order_by(AcceptCard.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_live_in_project(
        self, project_id: uuid.UUID, *, statuses: tuple[AcceptStatus, ...]
    ) -> list[AcceptCard]:
        """Every undecided card anywhere in a project, with its topic.

        Scoped by project rather than by topic because the question it answers
        is about *siblings*: two rooms in the same project each about to land a
        change (#314). Archived topics are excluded — their cards are already
        closed, and a room nobody is tracking cannot collide with anything.
        """
        stmt = (
            select(AcceptCard)
            .join(Topic, Topic.id == AcceptCard.topic_id)
            .where(
                Topic.project_id == project_id,
                Topic.status != TopicStatus.archived,
                AcceptCard.status.in_(statuses),
            )
            .order_by(AcceptCard.created_at)
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
