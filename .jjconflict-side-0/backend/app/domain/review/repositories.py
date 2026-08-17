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
        change_subject: str | None = None,
        change_body: str | None = None,
    ) -> AcceptCard:
        card = AcceptCard(
            topic_id=topic_id,
            reviewer_handle=reviewer_handle,
            routing_reason=routing_reason,
            status=status,
            change_subject=change_subject,
            change_body=change_body,
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

    async def list_live_for_topics(
        self, topic_ids: list[uuid.UUID], *, statuses: tuple[AcceptStatus, ...]
    ) -> list[AcceptCard]:
        """Undecided cards on ANY of these topics.

        By topic-set rather than by topic because archiving is cascading: the
        question this answers is "would archiving this sub-topic close a card
        somebody is still waiting on", and a grandchild's card is closed by the
        same cascade (`TopicService._archive_children`).
        """
        if not topic_ids:
            return []
        stmt = (
            select(AcceptCard)
            .where(
                AcceptCard.topic_id.in_(topic_ids),
                AcceptCard.status.in_(statuses),
            )
            .order_by(AcceptCard.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def reviewer_topic_ids(
        self, topic_ids: list[uuid.UUID], reviewer_handle: str
    ) -> dict[uuid.UUID, bool]:
        """{topic_id: is one of its cards still waiting on this reviewer} for
        every topic here that ever routed a card to them, in ONE query.

        Two facts in one row because they come from the same scan and the
        sidebar needs both: *being named* on a card is a lasting relationship
        with the topic (it stays yours after you accept it), while *pending* is
        the transient "this is on your desk right now". `pending` alone is the
        waiting state — a card in `pending_gate`/`gate_failed`/`conflict` is
        with 芝士, and one in `pr_open`/`accepted` has already been decided.
        """
        if not topic_ids:
            return {}
        stmt = (
            select(
                AcceptCard.topic_id,
                func.bool_or(AcceptCard.status == AcceptStatus.pending),
            )
            .where(
                AcceptCard.topic_id.in_(topic_ids),
                AcceptCard.reviewer_handle == reviewer_handle,
            )
            .group_by(AcceptCard.topic_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {topic_id: bool(waiting) for topic_id, waiting in rows}

    async def latest_decision_at(self, topic_ids: list[uuid.UUID]) -> datetime | None:
        """When a card on these topics last changed hands — NULL if there are no
        cards at all.

        `decided_at` first, `updated_at` as the fallback: a card condemned by
        the gate never gets a `decided_at` (nobody decided it), yet its moment
        is exactly what a "give them a window to re-file" clock has to start
        from.
        """
        if not topic_ids:
            return None
        stmt = select(
            func.max(func.coalesce(AcceptCard.decided_at, AcceptCard.updated_at))
        ).where(AcceptCard.topic_id.in_(topic_ids))
        return (await self._session.scalars(stmt)).first()

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
