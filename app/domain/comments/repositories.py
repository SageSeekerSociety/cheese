from datetime import UTC, datetime

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
        now = datetime.now(UTC).replace(tzinfo=None)
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
        comment.updated_at = datetime.now(UTC).replace(tzinfo=None)
        await self._session.flush()
        return comment

    async def soft_delete(self, comment: Comment) -> None:
        comment.deleted_at = datetime.now(UTC).replace(tzinfo=None)
        await self._session.flush()

    async def vote(self, *, comment_id: int, user_id: int, vote_type: str) -> Attitude:
        existing = await self._get_vote(comment_id, user_id)
        now = datetime.now(UTC).replace(tzinfo=None)
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

    async def bulk_count_votes(self, comment_ids: list[int]) -> dict[int, dict[str, int]]:
        """Aggregate POSITIVE/NEGATIVE counts for many comments in one query."""
        if not comment_ids:
            return {}
        stmt = (
            select(Attitude.attitudable_id, Attitude.attitude, func.count(Attitude.id))
            .where(
                Attitude.attitudable_id.in_(comment_ids),
                Attitude.attitudable_type == "COMMENT",
            )
            .group_by(Attitude.attitudable_id, Attitude.attitude)
        )
        result = await self._session.execute(stmt)
        out: dict[int, dict[str, int]] = {
            cid: {"POSITIVE": 0, "NEGATIVE": 0} for cid in comment_ids
        }
        for attitudable_id, attitude, count in result.all():
            out.setdefault(attitudable_id, {"POSITIVE": 0, "NEGATIVE": 0})[attitude] = count
        return out

    async def bulk_get_user_votes(self, comment_ids: list[int], user_id: int) -> dict[int, str]:
        if not comment_ids or user_id is None or user_id <= 0:
            return {}
        stmt: Select[tuple[Attitude]] = select(Attitude).where(
            Attitude.attitudable_id.in_(comment_ids),
            Attitude.attitudable_type == "COMMENT",
            Attitude.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        return {a.attitudable_id: a.attitude for a in result.scalars().all()}

    async def list_sub_comments(self, parent_comment_ids: list[int]) -> dict[int, list[Comment]]:
        """Fetch direct sub-comments (commentable_type=COMMENT) for the given parent ids."""
        if not parent_comment_ids:
            return {}
        stmt: Select[tuple[Comment]] = (
            select(Comment)
            .where(
                Comment.commentable_type == "COMMENT",
                Comment.commentable_id.in_(parent_comment_ids),
                Comment.deleted_at.is_(None),
            )
            .order_by(Comment.created_at.asc())
        )
        result = await self._session.execute(stmt)
        out: dict[int, list[Comment]] = {pid: [] for pid in parent_comment_ids}
        for row in result.scalars().all():
            out.setdefault(row.commentable_id, []).append(row)
        return out
