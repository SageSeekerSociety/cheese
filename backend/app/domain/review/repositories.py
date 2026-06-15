"""Accept card data access."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.review.models import AcceptCard


class AcceptCardRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        topic_id: uuid.UUID,
        reviewer_handle: str,
        routing_reason: str = "",
    ) -> AcceptCard:
        card = AcceptCard(
            topic_id=topic_id,
            reviewer_handle=reviewer_handle,
            routing_reason=routing_reason,
        )
        self._session.add(card)
        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def get(self, card_id: uuid.UUID) -> AcceptCard | None:
        return await self._session.get(AcceptCard, card_id)

    async def list_for_topic(self, topic_id: uuid.UUID) -> list[AcceptCard]:
        stmt = (
            select(AcceptCard)
            .where(AcceptCard.topic_id == topic_id)
            .order_by(AcceptCard.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())
