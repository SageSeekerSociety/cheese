import json
from datetime import UTC, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.discussion.models import (
    Discussion,
    DiscussionMentionedUser,
    DiscussionReaction,
    ReactionType,
)


def _content_str_to_json(content: str) -> dict:
    """Parse TipTap JSON string; fall back to wrapping as plain text doc."""
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    return {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": content}]}],
    }


class DiscussionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        model_type: str,
        model_id: int,
        sender_id: int,
        content: str,
        parent_id: int | None,
        mentioned_user_ids: list[int],
    ) -> Discussion:
        now = datetime.now(UTC).replace(tzinfo=None)
        content_json = _content_str_to_json(content)
        entity = Discussion(
            model_type=model_type,
            model_id=model_id,
            sender_id=sender_id,
            content=content_json,
            parent_id=parent_id,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(entity)
        await self._session.flush()

        for uid in mentioned_user_ids:
            mention = DiscussionMentionedUser(discussion_id=entity.id, user_id=uid)
            self._session.add(mention)
        if mentioned_user_ids:
            await self._session.flush()

        entity.mentioned_user_ids = mentioned_user_ids
        return entity

    async def get_by_id(self, discussion_id: int) -> Discussion | None:
        stmt: Select[tuple[Discussion]] = select(Discussion).where(
            Discussion.id == discussion_id,
            Discussion.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        entity = result.scalar_one_or_none()
        if entity is not None:
            entity.mentioned_user_ids = await self._load_mentioned_user_ids(entity.id)
        return entity

    async def _load_mentioned_user_ids(self, discussion_id: int) -> list[int]:
        stmt = select(DiscussionMentionedUser.user_id).where(
            DiscussionMentionedUser.discussion_id == discussion_id
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    async def find_all(
        self,
        *,
        model_type: str | None,
        model_id: int | None,
        parent_id: int | None,
        limit: int,
        offset: int,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[Discussion], int]:
        stmt: Select[tuple[Discussion]] = select(Discussion).where(Discussion.deleted_at.is_(None))
        if model_type is not None:
            stmt = stmt.where(Discussion.model_type == model_type)
        if model_id is not None:
            stmt = stmt.where(Discussion.model_id == model_id)
        if parent_id is None:
            stmt = stmt.where(Discussion.parent_id.is_(None))
        else:
            stmt = stmt.where(Discussion.parent_id == parent_id)

        order_column = Discussion.created_at if sort_by == "createdAt" else Discussion.updated_at
        if sort_order.lower() == "asc":
            stmt = stmt.order_by(order_column.asc())
        else:
            stmt = stmt.order_by(order_column.desc())

        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        for row in rows:
            row.mentioned_user_ids = await self._load_mentioned_user_ids(row.id)

        count_stmt = select(func.count(Discussion.id)).where(Discussion.deleted_at.is_(None))
        if model_type is not None:
            count_stmt = count_stmt.where(Discussion.model_type == model_type)
        if model_id is not None:
            count_stmt = count_stmt.where(Discussion.model_id == model_id)
        if parent_id is None:
            count_stmt = count_stmt.where(Discussion.parent_id.is_(None))
        else:
            count_stmt = count_stmt.where(Discussion.parent_id == parent_id)

        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return rows, total

    async def update_content(self, discussion_id: int, content_json: dict) -> Discussion | None:
        entity = await self.get_by_id(discussion_id)
        if entity is None:
            return None
        entity.content = content_json
        entity.updated_at = datetime.now(UTC).replace(tzinfo=None)
        await self._session.flush()
        return entity

    async def soft_delete(self, discussion_id: int) -> bool:
        entity = await self.get_by_id(discussion_id)
        if entity is None:
            return False
        entity.deleted_at = datetime.now(UTC).replace(tzinfo=None)
        await self._session.flush()
        return True

    async def count_children(self, parent_id: int) -> int:
        stmt = select(func.count(Discussion.id)).where(
            Discussion.parent_id == parent_id,
            Discussion.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)


class ReactionTypeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active(self) -> list[ReactionType]:
        stmt: Select[tuple[ReactionType]] = (
            select(ReactionType)
            .where(
                ReactionType.is_active.is_(True),
                ReactionType.deleted_at.is_(None),
            )
            .order_by(ReactionType.display_order.asc(), ReactionType.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, reaction_type_id: int) -> ReactionType | None:
        stmt: Select[tuple[ReactionType]] = select(ReactionType).where(
            ReactionType.id == reaction_type_id,
            ReactionType.deleted_at.is_(None),
            ReactionType.is_active.is_(True),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def ensure_defaults(self) -> None:
        stmt = select(func.count(ReactionType.id))
        result = await self._session.execute(stmt)
        if int(result.scalar_one() or 0) > 0:
            return
        now = datetime.now(UTC).replace(tzinfo=None)
        defaults = [
            ReactionType(
                code="LIKE",
                name="Like",
                description="thumbs up",
                display_order=0,
                is_active=True,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            ),
            ReactionType(
                code="CHEERS",
                name="Cheers",
                description="celebration",
                display_order=1,
                is_active=True,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            ),
        ]
        for rt in defaults:
            self._session.add(rt)
        await self._session.flush()


class DiscussionReactionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_reaction(
        self,
        *,
        discussion_id: int,
        user_id: int,
        reaction_type_id: int,
    ) -> DiscussionReaction | None:
        stmt: Select[tuple[DiscussionReaction]] = select(DiscussionReaction).where(
            DiscussionReaction.discussion_id == discussion_id,
            DiscussionReaction.user_id == user_id,
            DiscussionReaction.reaction_type_id == reaction_type_id,
            DiscussionReaction.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def toggle(
        self,
        *,
        discussion_id: int,
        user_id: int,
        reaction_type_id: int,
    ) -> DiscussionReaction | None:
        existing = await self.get_reaction(
            discussion_id=discussion_id,
            user_id=user_id,
            reaction_type_id=reaction_type_id,
        )
        now = datetime.now(UTC).replace(tzinfo=None)
        if existing is not None:
            existing.deleted_at = now
            existing.updated_at = now
            await self._session.flush()
            return None
        entity = DiscussionReaction(
            discussion_id=discussion_id,
            user_id=user_id,
            reaction_type_id=reaction_type_id,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def remove(
        self,
        *,
        discussion_id: int,
        user_id: int,
        reaction_type_id: int,
    ) -> bool:
        existing = await self.get_reaction(
            discussion_id=discussion_id,
            user_id=user_id,
            reaction_type_id=reaction_type_id,
        )
        if existing is None:
            return False
        now = datetime.now(UTC).replace(tzinfo=None)
        existing.deleted_at = now
        existing.updated_at = now
        await self._session.flush()
        return True

    async def count_by_discussion(self, discussion_id: int) -> dict[int, int]:
        stmt = (
            select(
                DiscussionReaction.reaction_type_id,
                func.count(DiscussionReaction.id),
            )
            .where(
                DiscussionReaction.discussion_id == discussion_id,
                DiscussionReaction.deleted_at.is_(None),
            )
            .group_by(DiscussionReaction.reaction_type_id)
        )
        result = await self._session.execute(stmt)
        return {row[0]: int(row[1]) for row in result.all()}

    async def has_user_reacted(
        self,
        *,
        discussion_id: int,
        user_id: int,
        reaction_type_id: int,
    ) -> bool:
        stmt = select(DiscussionReaction.id).where(
            DiscussionReaction.discussion_id == discussion_id,
            DiscussionReaction.user_id == user_id,
            DiscussionReaction.reaction_type_id == reaction_type_id,
            DiscussionReaction.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None
