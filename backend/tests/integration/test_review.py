"""Integration tests for the review domain (DB-backed)."""

import pytest
from anyio.from_thread import BlockingPortal
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError
from app.domain.review.models import ReviewStatus
from app.domain.review.repositories import ReviewRepository
from app.domain.review.services import ReviewService

PROJECT = 97001


class TestReview:
    @pytest.fixture(autouse=True)
    def setup(self, db_session: AsyncSession, _portal: BlockingPortal):
        self.db = db_session
        self.portal = _portal
        self.svc = ReviewService(ReviewRepository(db_session))

    def test_request_and_accept(self):
        async def _run():
            r = await self.svc.request_review(
                project_id=PROJECT, thread_id=10, requested_by_id=1
            )
            # capture state now: `r` and the decided object are the same identity-
            # mapped instance, so read the pre-decision facts before deciding.
            initial_status = r.status
            review_id = r.id
            pending_ids = [x.id for x in await self.svc.list_pending(PROJECT)]
            decided = await self.svc.decide(
                review_id=review_id, decided_by_id=2, accept=True, note="lgtm"
            )
            still_pending = await self.svc.list_pending(PROJECT)
            return initial_status, review_id, pending_ids, decided, still_pending

        initial_status, review_id, pending_ids, decided, still_pending = self.portal.call(_run)
        assert initial_status == ReviewStatus.PENDING.value
        assert pending_ids == [review_id]
        assert decided.status == ReviewStatus.ACCEPTED.value
        assert decided.decided_by_id == 2
        assert decided.note == "lgtm"
        assert decided.decided_at is not None
        assert still_pending == []  # no longer pending after decision

    def test_reject(self):
        async def _run():
            r = await self.svc.request_review(
                project_id=PROJECT, thread_id=11, requested_by_id=1
            )
            return await self.svc.decide(review_id=r.id, decided_by_id=2, accept=False)

        decided = self.portal.call(_run)
        assert decided.status == ReviewStatus.REJECTED.value

    def test_cannot_decide_twice(self):
        async def _run():
            r = await self.svc.request_review(
                project_id=PROJECT, thread_id=12, requested_by_id=1
            )
            await self.svc.decide(review_id=r.id, decided_by_id=2, accept=True)
            await self.svc.decide(review_id=r.id, decided_by_id=3, accept=False)

        with pytest.raises(BadRequestError):
            self.portal.call(_run)
