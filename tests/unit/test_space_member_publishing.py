"""Unit tests for app.domain.space.member_publishing_service.

Tests static helpers and _build_task_item logic without DB access.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.errors import BadRequestError, NotFoundError
from app.domain.space.member_publishing_service import (
    SpaceMemberPublishingService,
)

NOW = datetime(2025, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(**overrides):
    defaults = {
        "id": 10,
        "name": "Task One",
        "creator_id": 200,
        "category_id": 5,
        "space_id": 1,
        "approved": 0,
        "created_at": NOW,
        "deadline": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _category(**overrides):
    defaults = {"id": 5, "name": "Category A"}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _membership(**overrides):
    defaults = {
        "id": 1,
        "task_id": 10,
        "member_id": 100,
        "approved": 0,
        "completion_status": "NOT_SUBMITTED",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _submission(**overrides):
    defaults = {"id": 50, "membership_id": 1, "version": 1, "created_at": NOW}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _review(**overrides):
    defaults = {"id": 60, "submission_id": 50, "accepted": True, "score": 90}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_session():
    session = AsyncMock()
    return session


def _mock_scalar(val):
    m = MagicMock()
    m.scalar_one_or_none.return_value = val
    return m


def _mock_scalars(lst):
    m = MagicMock()
    s = MagicMock()
    s.all.return_value = lst
    m.scalars.return_value = s
    return m


# ---------------------------------------------------------------------------
# Static helper tests
# ---------------------------------------------------------------------------


class TestToTimestampMs:
    def test_none(self):
        assert SpaceMemberPublishingService._to_timestamp_ms(None) is None

    def test_value(self):
        result = SpaceMemberPublishingService._to_timestamp_ms(NOW)
        assert isinstance(result, int)
        assert result > 0


class TestParseTimestampParam:
    def test_none(self):
        assert SpaceMemberPublishingService._parse_timestamp_param(None, "from") is None

    def test_negative(self):
        with pytest.raises(BadRequestError):
            SpaceMemberPublishingService._parse_timestamp_param(-1, "from")

    def test_seconds_timestamp(self):
        ts = int(NOW.replace(tzinfo=UTC).timestamp())
        result = SpaceMemberPublishingService._parse_timestamp_param(ts, "from")
        assert result is not None

    def test_milliseconds_timestamp(self):
        ts = int(NOW.replace(tzinfo=UTC).timestamp() * 1000)
        result = SpaceMemberPublishingService._parse_timestamp_param(ts, "from")
        assert result is not None

    def test_invalid_timestamp(self):
        with pytest.raises(BadRequestError):
            SpaceMemberPublishingService._parse_timestamp_param(99999999999999999, "from")


class TestParseApprovedFilter:
    def test_none(self):
        assert SpaceMemberPublishingService._parse_approved_filter(None) is None

    def test_valid_approved(self):
        result = SpaceMemberPublishingService._parse_approved_filter("APPROVED")
        assert result == 0

    def test_valid_none_value(self):
        result = SpaceMemberPublishingService._parse_approved_filter("NONE")
        assert result == 2

    def test_invalid(self):
        with pytest.raises(BadRequestError):
            SpaceMemberPublishingService._parse_approved_filter("INVALID")


class TestNormalizeSortOrder:
    def test_asc(self):
        assert SpaceMemberPublishingService._normalize_sort_order("asc") == "asc"

    def test_desc(self):
        assert SpaceMemberPublishingService._normalize_sort_order("desc") == "desc"

    def test_invalid(self):
        with pytest.raises(BadRequestError):
            SpaceMemberPublishingService._normalize_sort_order("invalid")


class TestNormalizeSortBy:
    def test_valid(self):
        assert SpaceMemberPublishingService._normalize_sort_by("createdAt") == "createdAt"

    def test_participant_count(self):
        assert (
            SpaceMemberPublishingService._normalize_sort_by("participantCount")
            == "participantCount"
        )

    def test_invalid(self):
        with pytest.raises(BadRequestError):
            SpaceMemberPublishingService._normalize_sort_by("invalid")


# ---------------------------------------------------------------------------
# _build_task_item tests
# ---------------------------------------------------------------------------


class TestBuildTaskItem:
    def _svc(self):
        return SpaceMemberPublishingService(_mock_session())

    def test_no_memberships(self):
        svc = self._svc()
        task = _task(id=10)
        cat = _category(id=5)

        result = svc._build_task_item(
            task=task,
            category=cat,
            memberships=[],
            latest_submissions_by_membership_id={},
            reviews_by_submission_id={},
        )
        assert result["taskId"] == 10
        assert result["taskName"] == "Task One"
        assert result["participantCount"] == 0
        assert result["successRate"] == 0.0

    def test_with_approved_members(self):
        svc = self._svc()
        task = _task(id=10)
        cat = _category(id=5)
        m1 = _membership(id=1, approved=0, completion_status="NOT_SUBMITTED")
        m2 = _membership(id=2, approved=2, completion_status="NOT_SUBMITTED")  # NONE

        result = svc._build_task_item(
            task=task,
            category=cat,
            memberships=[m1, m2],
            latest_submissions_by_membership_id={},
            reviews_by_submission_id={},
        )
        assert result["participantCount"] == 2
        assert result["approvedParticipantCount"] == 1
        assert result["pendingParticipantApprovalCount"] == 1

    def test_with_submissions_and_reviews(self):
        svc = self._svc()
        task = _task(id=10)
        cat = _category(id=5)
        m = _membership(id=1, approved=0, completion_status="PENDING_REVIEW")
        sub = _submission(id=50, membership_id=1)
        rev = _review(id=60, submission_id=50, accepted=True)

        result = svc._build_task_item(
            task=task,
            category=cat,
            memberships=[m],
            latest_submissions_by_membership_id={1: sub},
            reviews_by_submission_id={50: rev},
        )
        assert result["submittedParticipantCount"] == 1
        assert result["submissionConversionRate"] == 1.0

    def test_success_status(self):
        svc = self._svc()
        task = _task(id=10)
        m = _membership(id=1, approved=0, completion_status="SUCCESS")
        sub = _submission(id=50, membership_id=1)

        result = svc._build_task_item(
            task=task,
            category=None,
            memberships=[m],
            latest_submissions_by_membership_id={1: sub},
            reviews_by_submission_id={},
        )
        assert result["successfulParticipantCount"] == 1
        assert result["category"]["name"] == ""

    def test_failed_status(self):
        svc = self._svc()
        task = _task(id=10)
        m = _membership(id=1, approved=0, completion_status="FAILED")

        result = svc._build_task_item(
            task=task,
            category=None,
            memberships=[m],
            latest_submissions_by_membership_id={},
            reviews_by_submission_id={},
        )
        assert result["failedParticipantCount"] == 1

    def test_rejected_resubmittable_status(self):
        svc = self._svc()
        task = _task(id=10)
        m = _membership(id=1, approved=0, completion_status="REJECTED_RESUBMITTABLE")

        result = svc._build_task_item(
            task=task,
            category=None,
            memberships=[m],
            latest_submissions_by_membership_id={},
            reviews_by_submission_id={},
        )
        assert result["failedParticipantCount"] == 1

    def test_review_rejected(self):
        svc = self._svc()
        task = _task(id=10)
        m = _membership(id=1, approved=0, completion_status="NOT_SUBMITTED")
        sub = _submission(id=50, membership_id=1)
        rev = _review(id=60, submission_id=50, accepted=False)

        result = svc._build_task_item(
            task=task,
            category=None,
            memberships=[m],
            latest_submissions_by_membership_id={1: sub},
            reviews_by_submission_id={50: rev},
        )
        assert result["failedParticipantCount"] == 1


# ---------------------------------------------------------------------------
# Public API tests
# ---------------------------------------------------------------------------


class TestGetMyPublishingOverview:
    @pytest.mark.anyio
    async def test_space_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        svc = SpaceMemberPublishingService(session)

        with pytest.raises(NotFoundError):
            await svc.get_my_publishing_overview(space_id=999, user_id=100)

    @pytest.mark.anyio
    async def test_empty_tasks(self):
        session = _mock_session()
        space = SimpleNamespace(id=1, deleted_at=None)
        session.execute.side_effect = [
            _mock_scalar(space),  # _ensure_space_exists
            _mock_scalars([]),  # _list_my_publishing_tasks
        ]
        svc = SpaceMemberPublishingService(session)

        result = await svc.get_my_publishing_overview(space_id=1, user_id=100)
        assert result["spaceId"] == 1
        assert result["taskCount"] == 0


class TestGetMyPublishedTasks:
    @pytest.mark.anyio
    async def test_space_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        svc = SpaceMemberPublishingService(session)

        with pytest.raises(NotFoundError):
            await svc.get_my_published_tasks(space_id=999, user_id=100)

    @pytest.mark.anyio
    async def test_invalid_approved(self):
        session = _mock_session()
        space = SimpleNamespace(id=1, deleted_at=None)
        session.execute.return_value = _mock_scalar(space)
        svc = SpaceMemberPublishingService(session)

        with pytest.raises(BadRequestError):
            await svc.get_my_published_tasks(space_id=1, user_id=100, approved="BAD")

    @pytest.mark.anyio
    async def test_invalid_sort_by(self):
        session = _mock_session()
        space = SimpleNamespace(id=1, deleted_at=None)
        session.execute.return_value = _mock_scalar(space)
        svc = SpaceMemberPublishingService(session)

        with pytest.raises(BadRequestError):
            await svc.get_my_published_tasks(space_id=1, user_id=100, sort_by="bad")

    @pytest.mark.anyio
    async def test_invalid_sort_order(self):
        session = _mock_session()
        space = SimpleNamespace(id=1, deleted_at=None)
        session.execute.return_value = _mock_scalar(space)
        svc = SpaceMemberPublishingService(session)

        with pytest.raises(BadRequestError):
            await svc.get_my_published_tasks(space_id=1, user_id=100, sort_order="bad")

    @pytest.mark.anyio
    async def test_empty_result(self):
        session = _mock_session()
        space = SimpleNamespace(id=1, deleted_at=None)
        session.execute.side_effect = [
            _mock_scalar(space),
            _mock_scalars([]),  # no tasks
        ]
        svc = SpaceMemberPublishingService(session)

        result = await svc.get_my_published_tasks(space_id=1, user_id=100)
        assert result == []
