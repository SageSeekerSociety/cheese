"""结论卡 data access."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.conclusion.models import (
    ARCHIVE_DEFERRED,
    SETTLED_STATES,
    ConclusionCard,
    ConclusionStatus,
)

#: 未结算 = 还欠某个话题/某一轮一个动作。`returned` 也算：子话题还得补证据。
LIVE_STATES = (ConclusionStatus.open, ConclusionStatus.returned)


class ConclusionCardRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        receiver_topic_id: uuid.UUID,
        conclusion: str,
        digest_deadline_at: datetime,
    ) -> ConclusionCard:
        card = ConclusionCard(
            project_id=project_id,
            topic_id=topic_id,
            receiver_topic_id=receiver_topic_id,
            conclusion=conclusion,
            digest_deadline_at=digest_deadline_at,
        )
        self._session.add(card)
        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def get(self, card_id: uuid.UUID) -> ConclusionCard | None:
        return await self._session.get(ConclusionCard, card_id)

    async def live_for_topic(self, topic_id: uuid.UUID) -> ConclusionCard | None:
        """The sub-topic's card that still needs something to happen to it.

        At most one exists by construction (opening a new card supersedes the
        previous one), so the newest live row IS the live card.
        """
        stmt = (
            select(ConclusionCard)
            .where(
                ConclusionCard.topic_id == topic_id,
                ConclusionCard.status.in_(LIVE_STATES),
            )
            .order_by(ConclusionCard.created_at.desc())
        )
        return (await self._session.scalars(stmt)).first()

    async def list_for_topic(self, topic_id: uuid.UUID) -> list[ConclusionCard]:
        stmt = (
            select(ConclusionCard)
            .where(ConclusionCard.topic_id == topic_id)
            .order_by(ConclusionCard.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_open_for_receiver(
        self, receiver_topic_id: uuid.UUID, *, created_before: datetime | None = None
    ) -> list[ConclusionCard]:
        """Open cards this parent owes a verdict on.

        ``created_before`` exists for the turn-end sweep: only cards that already
        existed when the turn STARTED were visible to it. A card born mid-turn
        (a second sub-topic concluding while the parent was busy) must survive to
        the digest turn that will actually read it, instead of being auto-accepted
        by a turn that never saw it.
        """
        stmt = select(ConclusionCard).where(
            ConclusionCard.receiver_topic_id == receiver_topic_id,
            ConclusionCard.status == ConclusionStatus.open,
        )
        if created_before is not None:
            stmt = stmt.where(ConclusionCard.created_at <= created_before)
        return list(
            (
                await self._session.scalars(stmt.order_by(ConclusionCard.created_at))
            ).all()
        )

    async def list_expired(self, now: datetime) -> list[ConclusionCard]:
        """Open cards past their absolute deadline, across all projects."""
        stmt = (
            select(ConclusionCard)
            .where(
                ConclusionCard.status == ConclusionStatus.open,
                ConclusionCard.digest_deadline_at <= now,
            )
            .order_by(ConclusionCard.digest_deadline_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_archive_deferred(self) -> list[ConclusionCard]:
        """采信了、但归档还欠着的卡（`ARCHIVE_DEFERRED` 那个哨兵值）。

        跨项目一把捞：这是个兜底扫描，量极小——只有"结论已采信 + 还挂着未决
        验收卡"的子话题才会出现在这里，而它们同时也是有人正盯着的那几个。
        """
        stmt = (
            select(ConclusionCard)
            .where(
                ConclusionCard.status == ConclusionStatus.accepted,
                ConclusionCard.settle_reason == ARCHIVE_DEFERRED,
            )
            .order_by(ConclusionCard.settled_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_accepted(self, *, limit: int) -> list[ConclusionCard]:
        """采信过的卡，最近的在前 —— 「提交并进母话题分支」那条重试队列的输入。

        队列本身不存库：一张卡还欠不欠合并，git 自己答得出来（提交在不在母话题
        分支上），所以这里只负责把候选捞出来，判读在 `room_branch` 里。`limit`
        是防跑飞的护栏而不是策略——真正把这条扫描压到近乎零成本的是磁盘上那张
        「这张卡合完了」的备忘，绝大多数候选连 git 都不用问就跳过了。
        """
        stmt = (
            select(ConclusionCard)
            .where(ConclusionCard.status == ConclusionStatus.accepted)
            .order_by(ConclusionCard.settled_at.desc())
            .limit(limit)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_live_under(self, topic_ids: list[uuid.UUID]) -> list[ConclusionCard]:
        """Unsettled cards produced by any of these topics (archive cascade)."""
        if not topic_ids:
            return []
        stmt = (
            select(ConclusionCard)
            .where(
                ConclusionCard.topic_id.in_(topic_ids),
                ConclusionCard.status.notin_(SETTLED_STATES),
            )
            .order_by(ConclusionCard.created_at)
        )
        return list((await self._session.scalars(stmt)).all())
