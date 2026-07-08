"""Data access for 消息表情回应 (message reactions).

Data-access only: no authorization, no business rules. A reaction is a
``(block_id, user_id, emoji)`` triple; the unique constraint makes ``add`` idempotent
(ON CONFLICT DO NOTHING) so react/unreact are naturally re-runnable.
"""

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.reaction.models import MessageReaction


class ReactionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, block_id: int, user_id: int, emoji: str) -> MessageReaction:
        """Idempotent add — a duplicate ``(block, user, emoji)`` is a no-op that returns
        the already-existing row (ON CONFLICT DO NOTHING)."""
        stmt = (
            pg_insert(MessageReaction)
            .values(
                block_id=block_id,
                user_id=user_id,
                emoji=emoji,
                created_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(constraint="uq_reaction_block_user_emoji")
        )
        await self._session.execute(stmt)
        await self._session.flush()
        row = await self._get(block_id, user_id, emoji)
        # Uniqueness guarantees a row exists after the insert-or-nothing.
        assert row is not None
        return row

    async def _get(self, block_id: int, user_id: int, emoji: str) -> MessageReaction | None:
        return (
            await self._session.execute(
                select(MessageReaction).where(
                    MessageReaction.block_id == block_id,
                    MessageReaction.user_id == user_id,
                    MessageReaction.emoji == emoji,
                )
            )
        ).scalar_one_or_none()

    async def remove(self, block_id: int, user_id: int, emoji: str) -> bool:
        """Remove one reaction. Returns True if a row was deleted, False if none matched."""
        result = await self._session.execute(
            delete(MessageReaction)
            .where(
                MessageReaction.block_id == block_id,
                MessageReaction.user_id == user_id,
                MessageReaction.emoji == emoji,
            )
            .returning(MessageReaction.id)
        )
        await self._session.flush()
        return result.scalar_one_or_none() is not None

    async def list_for_block(self, block_id: int) -> list[MessageReaction]:
        rows = (
            await self._session.execute(
                select(MessageReaction)
                .where(MessageReaction.block_id == block_id)
                .order_by(MessageReaction.id)
            )
        ).scalars()
        return list(rows)

    async def list_for_blocks(self, block_ids: list[int]) -> list[MessageReaction]:
        if not block_ids:
            return []
        rows = (
            await self._session.execute(
                select(MessageReaction)
                .where(MessageReaction.block_id.in_(block_ids))
                .order_by(MessageReaction.id)
            )
        ).scalars()
        return list(rows)
