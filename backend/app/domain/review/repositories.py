"""Accept card data access."""

import uuid
from datetime import datetime

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.review.models import AcceptApproval, AcceptCard, AcceptStatus
from app.domain.room_task.models import Task, TaskStatus
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
        task_id: uuid.UUID | None = None,
        delivered_task_ids: list[uuid.UUID] | None = None,
    ) -> AcceptCard:
        # 递卡是房间的事 —— 一棵树 = 一个分支 = 一个 PR = 一批活, and the batch
        # belongs to the room, not to any one card in it.
        card = AcceptCard(
            topic_id=topic_id,
            task_id=task_id,
            reviewer_handle=reviewer_handle,
            routing_reason=routing_reason,
            status=status,
            change_subject=change_subject,
            change_body=change_body,
            delivered_task_ids=[str(t) for t in (delivered_task_ids or [])],
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

    async def clear_approvals(self, card_id: uuid.UUID) -> None:
        """新提交作废已有的采纳 (#718, dismiss_stale): drop every vote this
        card has collected — they were cast on a head that no longer exists."""
        await self._session.execute(
            delete(AcceptApproval).where(AcceptApproval.card_id == card_id)
        )
        await self._session.flush()

    async def get(self, card_id: uuid.UUID) -> AcceptCard | None:
        return await self._session.get(AcceptCard, card_id)

    async def list_for_task(self, task_id: uuid.UUID) -> list[AcceptCard]:
        """Every card that has ever delivered this tree, newest first.

        The scope "one card at a time" is really about: a tree has one branch
        and therefore one PR, and two live cards on it would be two PRs racing
        each other on the same commits.
        """
        stmt = (
            select(AcceptCard)
            .where(AcceptCard.task_id == task_id)
            .order_by(AcceptCard.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_for_topic(self, topic_id: uuid.UUID) -> list[AcceptCard]:
        """The cards this room filed.

        `task_id IS NULL` is not redundant: cards filed back when a piece of
        work was a place of its own sit under the same room, and a room asking
        "do I have a card" must not be answered with one of those.
        """
        stmt = (
            select(AcceptCard)
            .where(
                AcceptCard.topic_id == topic_id,
            )
            .order_by(AcceptCard.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_everywhere_in_room(self, topic_id: uuid.UUID) -> list[AcceptCard]:
        """Every card filed anywhere in this room — its own and its cards'.

        The room's own set (`list_for_topic`) is the answer to "do I have a
        card". This is the answer to "what is still open in here", which is a
        different question and has to include what a piece of work filed back
        when work was a place: an unsettled row riding a PR is one the poller
        keeps following, and archiving the room is exactly when that must stop.
        """
        stmt = (
            select(AcceptCard)
            .where(AcceptCard.topic_id == topic_id)
            .order_by(AcceptCard.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def latest_by_task(
        self, task_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, AcceptCard]:
        """The newest card on each of these threads, in ONE query.

        Batched on purpose. The rail shows every thread with the PR it rides
        on, and asking per thread is the N+1 that turns one sidebar into one
        request per piece of work ever dispatched. Newest wins because a thread
        can file again after a rejection, and the current card is the one that
        says where the work stands.
        """
        if not task_ids:
            return {}
        stmt = (
            select(AcceptCard)
            .where(AcceptCard.task_id.in_(task_ids))
            .order_by(AcceptCard.created_at, AcceptCard.id)
        )
        latest: dict[uuid.UUID, AcceptCard] = {}
        for card in (await self._session.scalars(stmt)).all():
            # Ordered oldest-first, so the last write per key is the newest.
            if card.task_id is not None:
                latest[card.task_id] = card
        return latest

    async def list_live_for_places(
        self, place_ids: list[uuid.UUID], *, statuses: tuple[AcceptStatus, ...]
    ) -> list[AcceptCard]:
        """Undecided cards on ANY of these places — rooms or threads.

        Matched on EITHER key. A card filed from a thread stores the room in
        `topic_id` and the thread in `task_id`, so asking only about `topic_id`
        answers "no card" for every thread — and the caller is
        `anybody_still_waiting`, whose "no" closes the place and revokes the very
        card the reviewer had not seen yet. That is the 2026-08-16 incident this
        whole guard was written for, one shape over.
        """
        if not place_ids:
            return []
        stmt = (
            select(AcceptCard)
            .where(
                or_(
                    AcceptCard.topic_id.in_(place_ids),
                    AcceptCard.task_id.in_(place_ids),
                ),
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
        with 芝士, and an `accepted` one has already been decided.
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

    async def latest_decision_at(self, place_ids: list[uuid.UUID]) -> datetime | None:
        """When a card on these places last changed hands — NULL if there are no
        cards at all.

        `decided_at` first, `updated_at` as the fallback: a card condemned by
        the gate never gets a `decided_at` (nobody decided it), yet its moment
        is exactly what a "give them a window to re-file" clock has to start
        from.
        """
        if not place_ids:
            return None
        stmt = select(
            func.max(func.coalesce(AcceptCard.decided_at, AcceptCard.updated_at))
        ).where(
            or_(
                AcceptCard.topic_id.in_(place_ids),
                AcceptCard.task_id.in_(place_ids),
            )
        )
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

    async def list_awaiting_merge_on_active_topics(self) -> list[AcceptCard]:
        """Pending PRs and returned batches that can still merge externally.

        孤儿卡修复 (2026-08-10): the topic's status is part of the predicate, not
        just the card's. Without the join this returned cards on ARCHIVED topics
        too, and the poller kept driving them every 60s with GitHub credentials
        — for work nobody is tracking any more. `TopicService._archive_one`
        closes those cards at archive time; this join is the second lock,
        covering rows that predate the fix or arrive by some future archive
        path.
        """
        stmt = (
            select(AcceptCard)
            .join(Topic, Topic.id == AcceptCard.topic_id)
            .outerjoin(Task, Task.id == AcceptCard.task_id)
            .where(
                or_(
                    AcceptCard.status == AcceptStatus.pending,
                    and_(
                        AcceptCard.status == AcceptStatus.rejected,
                        AcceptCard.pr_merged_at.is_(None),
                        Task.status == TaskStatus.open,
                    ),
                ),
                AcceptCard.pr_number.is_not(None),
                Topic.status != TopicStatus.archived,
            )
            .order_by(
                (AcceptCard.status == AcceptStatus.pending).desc(),
                AcceptCard.created_at.desc(),
            )
        )
        cards = []
        seen_trees: set[uuid.UUID] = set()
        for card in (await self._session.scalars(stmt)).all():
            # A resubmitted batch belongs to its pending card; otherwise use
            # its latest return instead of polling every historical review.
            if card.task_id is not None:
                if card.task_id in seen_trees:
                    continue
                seen_trees.add(card.task_id)
            cards.append(card)
        return cards
