"""Data access for reviews. No business logic."""

from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.review.models import Review, ReviewStatus


class ReviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, project_id: int, thread_id: int, requested_by_id: int
    ) -> Review:
        now = datetime.now(UTC)
        review = Review(
            project_id=project_id,
            thread_id=thread_id,
            status=ReviewStatus.PENDING.value,
            requested_by_id=requested_by_id,
            decided_by_id=None,
            decided_at=None,
            note="",
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(review)
        await self._session.flush()
        return review

    async def get_by_id(self, review_id: int) -> Review | None:
        stmt: Select[tuple[Review]] = select(Review).where(
            Review.id == review_id, Review.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_pending(self, project_id: int) -> list[Review]:
        stmt: Select[tuple[Review]] = (
            select(Review)
            .where(
                Review.project_id == project_id,
                Review.status == ReviewStatus.PENDING.value,
                Review.deleted_at.is_(None),
            )
            .order_by(Review.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def decide(
        self, review: Review, *, status: ReviewStatus, decided_by_id: int, note: str
    ) -> Review:
        now = datetime.now(UTC)
        review.status = status.value
        review.decided_by_id = decided_by_id
        review.decided_at = now
        review.note = note
        review.updated_at = now
        await self._session.flush()
        return review
