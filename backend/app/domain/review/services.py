"""Business logic for reviews (验收)."""

from app.core.errors import BadRequestError, NotFoundError
from app.domain.review.models import Review, ReviewStatus
from app.domain.review.repositories import ReviewRepository


class ReviewService:
    def __init__(self, repo: ReviewRepository) -> None:
        self._repo = repo

    async def _require(self, review_id: int) -> Review:
        review = await self._repo.get_by_id(review_id)
        if review is None:
            raise NotFoundError(f"Review {review_id} not found")
        return review

    async def request_review(
        self, *, project_id: int, thread_id: int, requested_by_id: int
    ) -> Review:
        return await self._repo.create(
            project_id=project_id, thread_id=thread_id, requested_by_id=requested_by_id
        )

    async def get_review(self, review_id: int) -> Review:
        return await self._require(review_id)

    async def list_pending(self, project_id: int) -> list[Review]:
        return await self._repo.list_pending(project_id)

    async def decide(
        self, *, review_id: int, decided_by_id: int, accept: bool, note: str = ""
    ) -> Review:
        review = await self._require(review_id)
        if review.status != ReviewStatus.PENDING.value:
            raise BadRequestError("review has already been decided")
        status = ReviewStatus.ACCEPTED if accept else ReviewStatus.REJECTED
        return await self._repo.decide(
            review, status=status, decided_by_id=decided_by_id, note=note
        )
