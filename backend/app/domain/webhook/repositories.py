"""Webhook credential data access."""

import uuid

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.webhook.models import WebhookToken


class WebhookTokenRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    @staticmethod
    def _at(topic_id: uuid.UUID, task_id: uuid.UUID | None):
        return (
            WebhookToken.topic_id == topic_id,
            WebhookToken.task_id.is_(None)
            if task_id is None
            else WebhookToken.task_id == task_id,
        )

    async def bump_version(
        self,
        topic_id: uuid.UUID,
        project_id: uuid.UUID,
        *,
        task_id: uuid.UUID | None = None,
    ) -> int:
        """Mint/rotate: create the row at version 1, or increment it. Returns the
        new version — the value the freshly-minted token must embed.

        The conflict target is one of the two PARTIAL unique indexes, named by
        repeating its predicate: a room's row and a thread's row are unique on
        different columns, because `task_id` is NULL on every room row and NULL
        is not equal to NULL in a unique index. Postgres refuses a conflict
        target it cannot match to a real index, so getting the predicate wrong
        raises instead of quietly inserting a second row.
        """
        room_half = task_id is None
        stmt = (
            insert(WebhookToken)
            .values(
                topic_id=topic_id,
                project_id=project_id,
                task_id=task_id,
                version=1,
            )
            .on_conflict_do_update(
                index_elements=[WebhookToken.topic_id]
                if room_half
                else [WebhookToken.task_id],
                index_where=text("task_id IS NULL")
                if room_half
                else text("task_id IS NOT NULL"),
                set_={"version": WebhookToken.version + 1},
            )
            .returning(WebhookToken.version)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.scalar_one()

    async def current_version(
        self, topic_id: uuid.UUID, *, task_id: uuid.UUID | None = None
    ) -> int | None:
        stmt = select(WebhookToken.version).where(*self._at(topic_id, task_id))
        return (await self._session.scalars(stmt)).first()

    async def revoke(
        self, topic_id: uuid.UUID, *, task_id: uuid.UUID | None = None
    ) -> bool:
        """Invalidate every token minted so far for this place without minting a
        replacement (bump with no corresponding token handed out)."""
        stmt = select(WebhookToken).where(*self._at(topic_id, task_id))
        row = (await self._session.scalars(stmt)).first()
        if row is None:
            return False
        row.version += 1
        await self._session.flush()
        return True
