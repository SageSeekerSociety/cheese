"""Webhook credential data access."""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.webhook.models import WebhookToken


class WebhookTokenRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def bump_version(self, topic_id: uuid.UUID, project_id: uuid.UUID) -> int:
        """Mint/rotate: create the row at version 1, or increment it. Returns the
        new version — the value the freshly-minted token must embed."""
        stmt = (
            insert(WebhookToken)
            .values(topic_id=topic_id, project_id=project_id, version=1)
            .on_conflict_do_update(
                index_elements=[WebhookToken.topic_id],
                set_={"version": WebhookToken.version + 1},
            )
            .returning(WebhookToken.version)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.scalar_one()

    async def current_version(self, topic_id: uuid.UUID) -> int | None:
        stmt = select(WebhookToken.version).where(WebhookToken.topic_id == topic_id)
        return (await self._session.scalars(stmt)).first()

    async def revoke(self, topic_id: uuid.UUID) -> bool:
        """Invalidate every token minted so far for this topic without minting a
        replacement (bump with no corresponding token handed out)."""
        row = await self._session.get(WebhookToken, topic_id)
        if row is None:
            return False
        row.version += 1
        await self._session.flush()
        return True
