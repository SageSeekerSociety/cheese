from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comments.models import Comment
from app.domain.questions.models import Attitude


class CommentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_comments(
        self,
        *,
        commentable_type: str,
        commentable_id: int,
        limit: int,
        offset: int,
    ) -> tuple[list[Comment], int]:
        stmt: Select[tuple[Comment]] = (
            select(Comment)
            .where(
                Comment.commentable_type == commentable_type,
                Comment.commentable_id == commentable_id,
                Comment.deleted_at.is_(None),
            )
            .order_by(Comment.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        count_stmt = select(func.count(Comment.id)).where(
            Comment.commentable_type == commentable_type,
            Comment.commentable_id == commentable_id,
            Comment.deleted_at.is_(None),
        )
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return rows, total

    async def get_by_id(self, comment_id: int) -> Comment | None:
        stmt: Select[tuple[Comment]] = select(Comment).where(
            Comment.id == comment_id,
            Comment.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        commentable_type: str,
        commentable_id: int,
        content: str,
        created_by_id: int,
    ) -> Comment:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        comment = Comment(
            commentable_type=commentable_type,
            commentable_id=commentable_id,
            content=content,
            created_by_id=created_by_id,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(comment)
        await self._session.flush()
        return comment

    async def update(self, comment: Comment, *, content: str | None = None) -> Comment:
        if content is not None:
            comment.content = content
        comment.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self._session.flush()
        return comment

    async def soft_delete(self, comment: Comment) -> None:
        comment.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self._session.flush()

    async def vote(self, *, comment_id: int, user_id: int, vote_type: str) -> Attitude:
        existing = await self._get_vote(comment_id, user_id)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if existing is not None:
            existing.attitude = vote_type
            existing.updated_at = now
            await self._session.flush()
            return existing
        attitude = Attitude(
            attitudable_id=comment_id,
            attitudable_type="COMMENT",
            user_id=user_id,
            attitude=vote_type,
            created_at=now,
            updated_at=now,
        )
        self._session.add(attitude)
        await self._session.flush()
        return attitude

    async def remove_vote(self, *, comment_id: int, user_id: int) -> bool:
        existing = await self._get_vote(comment_id, user_id)
        if existing is None:
            return False
        await self._session.delete(existing)
        await self._session.flush()
        return True

    async def _get_vote(self, comment_id: int, user_id: int) -> Attitude | None:
        stmt: Select[tuple[Attitude]] = select(Attitude).where(
            Attitude.attitudable_id == comment_id,
            Attitude.attitudable_type == "COMMENT",
            Attitude.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_vote(self, comment_id: int, user_id: int) -> str | None:
        vote = await self._get_vote(comment_id, user_id)
        return vote.attitude if vote else None

    async def count_votes(self, comment_id: int) -> dict[str, int]:
        stmt = (
            select(Attitude.attitude, func.count(Attitude.id))
            .where(
                Attitude.attitudable_id == comment_id,
                Attitude.attitudable_type == "COMMENT",
            )
            .group_by(Attitude.attitude)
        )
        result = await self._session.execute(stmt)
        counts = {"POSITIVE": 0, "NEGATIVE": 0}
        for attitude, count in result.all():
            counts[attitude] = count
        return counts
