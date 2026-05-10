"""Unit tests for app.domain.space.member_participating_service.

Tests static helpers and row construction logic without DB access.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.errors import BadRequestError, NotFoundError
from app.domain.space.member_participating_service import (
    SpaceMemberParticipatingService,
    _Context,
)

NOW = datetime(2025, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _membership(**overrides):
    defaults = {
        "id": 1,
        "task_id": 10,
        "member_id": 100,
        "is_team": False,
        "approved": 0,  # APPROVED
        "completion_status": "NOT_SUBMITTED",
        "deadline": None,
        "created_at": NOW,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _task(**overrides):
    defaults = {
        "id": 10,
        "name": "Task One",
        "creator_id": 200,
        "category_id": 5,
        "space_id": 1,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _category(**overrides):
    defaults = {"id": 5, "name": "Category A"}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _user(**overrides):
    defaults = {"id": 200, "username": "publisher"}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _team(**overrides):
    defaults = {"id": 50, "name": "Team Alpha"}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _context(**overrides):
    """Build a minimal _Context for unit testing _build_row."""
    defaults = {
        "memberships": [],
        "tasks_by_id": {},
        "categories_by_id": {},
        "creators_by_id": {},
        "teams_by_id": {},
        "admin_team_ids": set(),
        "submissions_by_membership_id": {},
        "reviews_by_submission_id": {},
        "current_user_id": 100,
    }
    defaults.update(overrides)
    return _Context(**defaults)


def _mock_session():
    session = AsyncMock()
    return session


def _mock_scalars(lst):
    m = MagicMock()
    s = MagicMock()
    s.all.return_value = lst
    m.scalars.return_value = s
    return m


def _mock_scalar(val):
    m = MagicMock()
    m.scalar_one_or_none.return_value = val
    return m


# ---------------------------------------------------------------------------
# Static helper tests
# ---------------------------------------------------------------------------


class TestParseApprovedFilter:
    def test_none(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        assert svc._parse_approved_filter(None) is None

    def test_empty_string(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        assert svc._parse_approved_filter("") is None

    def test_valid_approved(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        assert svc._parse_approved_filter("APPROVED") == "APPROVED"

    def test_valid_none_value(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        assert svc._parse_approved_filter("NONE") == "NONE"

    def test_valid_disapproved(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        assert svc._parse_approved_filter("DISAPPROVED") == "DISAPPROVED"

    def test_invalid(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        with pytest.raises(BadRequestError):
            svc._parse_approved_filter("INVALID")


class TestParseCompletionStatusFilter:
    def test_none(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        assert svc._parse_completion_status_filter(None) is None

    def test_empty(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        assert svc._parse_completion_status_filter("") is None

    def test_valid(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        assert svc._parse_completion_status_filter("PENDING_REVIEW") == "PENDING_REVIEW"

    def test_valid_success(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        assert svc._parse_completion_status_filter("SUCCESS") == "SUCCESS"

    def test_invalid(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        with pytest.raises(BadRequestError):
            svc._parse_completion_status_filter("INVALID")


class TestParseIdentityTypeFilter:
    def test_none(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        assert svc._parse_identity_type_filter(None) is None

    def test_empty(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        assert svc._parse_identity_type_filter("") is None

    def test_user(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        assert svc._parse_identity_type_filter("USER") == "USER"

    def test_team(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        assert svc._parse_identity_type_filter("TEAM") == "TEAM"

    def test_invalid(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        with pytest.raises(BadRequestError):
            svc._parse_identity_type_filter("INVALID")


class TestNormalizeSortBy:
    def test_valid_joined_at(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        assert svc._normalize_sort_by("joinedAt") == "joinedAt"

    def test_valid_deadline(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        assert svc._normalize_sort_by("deadline") == "deadline"

    def test_invalid(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        with pytest.raises(BadRequestError):
            svc._normalize_sort_by("invalid")


class TestNormalizeSortOrder:
    def test_asc(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        assert svc._normalize_sort_order("asc") == "asc"

    def test_desc(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        assert svc._normalize_sort_order("desc") == "desc"

    def test_invalid(self):
        svc = SpaceMemberParticipatingService(_mock_session())
        with pytest.raises(BadRequestError):
            svc._normalize_sort_order("invalid")


class TestToTimestampMs:
    def test_none(self):
        assert SpaceMemberParticipatingService._to_timestamp_ms(None) is None

    def test_datetime(self):
        dt = datetime(2025, 1, 1, 0, 0, 0)
        result = SpaceMemberParticipatingService._to_timestamp_ms(dt)
        assert isinstance(result, int)
        assert result > 0


# ---------------------------------------------------------------------------
# _build_row tests
# ---------------------------------------------------------------------------


class TestBuildRow:
    def _svc(self):
        return SpaceMemberParticipatingService(_mock_session())

    def test_basic_user_membership(self):
        svc = self._svc()
        m = _membership(id=1, task_id=10, member_id=100, is_team=False, approved=0)
        t = _task(id=10, creator_id=200)
        cat = _category(id=5)
        user = _user(id=200)
        ctx = _context(
            tasks_by_id={10: t},
            categories_by_id={5: cat},
            creators_by_id={200: user},
            current_user_id=100,
        )

        row = svc._build_row(m, ctx)
        assert row["taskId"] == 10
        assert row["taskName"] == "Task One"
        assert row["identityType"] == "USER"
        assert row["approved"] == "APPROVED"
        assert row["completionStatus"] == "NOT_SUBMITTED"
        assert row["canSubmit"] is True
        assert row["teamName"] is None

    def test_team_membership(self):
        svc = self._svc()
        m = _membership(id=2, task_id=10, member_id=50, is_team=True, approved=0)
        t = _task(id=10)
        cat = _category(id=5)
        user = _user(id=200)
        team = _team(id=50)
        ctx = _context(
            tasks_by_id={10: t},
            categories_by_id={5: cat},
            creators_by_id={200: user},
            teams_by_id={50: team},
            admin_team_ids={50},
            current_user_id=100,
        )

        row = svc._build_row(m, ctx)
        assert row["identityType"] == "TEAM"
        assert row["teamName"] == "Team Alpha"
        assert row["canSubmit"] is True

    def test_team_not_admin_cannot_submit(self):
        svc = self._svc()
        m = _membership(id=2, task_id=10, member_id=50, is_team=True, approved=0)
        t = _task(id=10)
        team = _team(id=50)
        ctx = _context(
            tasks_by_id={10: t},
            categories_by_id={},
            creators_by_id={},
            teams_by_id={50: team},
            admin_team_ids=set(),  # not admin
            current_user_id=100,
        )

        row = svc._build_row(m, ctx)
        assert row["canSubmit"] is False

    def test_not_approved_cannot_submit(self):
        svc = self._svc()
        m = _membership(id=1, task_id=10, member_id=100, is_team=False, approved=2)  # NONE
        t = _task(id=10)
        ctx = _context(
            tasks_by_id={10: t},
            categories_by_id={},
            creators_by_id={},
            current_user_id=100,
        )

        row = svc._build_row(m, ctx)
        assert row["canSubmit"] is False
        assert row["approved"] == "NONE"

    def test_with_submission_and_review(self):
        svc = self._svc()
        m = _membership(id=1, task_id=10, member_id=100, approved=0, completion_status="SUCCESS")
        t = _task(id=10)
        sub = SimpleNamespace(id=100, membership_id=1, created_at=NOW)
        review = SimpleNamespace(id=200, submission_id=100, accepted=True, score=95)
        ctx = _context(
            tasks_by_id={10: t},
            categories_by_id={},
            creators_by_id={},
            submissions_by_membership_id={1: sub},
            reviews_by_submission_id={100: review},
            current_user_id=100,
        )

        row = svc._build_row(m, ctx)
        assert row["completionStatus"] == "SUCCESS"
        assert row["latestReviewAccepted"] is True
        assert row["latestReviewScore"] == 95.0

    def test_task_not_found_fallback(self):
        svc = self._svc()
        m = _membership(id=1, task_id=999, member_id=100, approved=0)
        ctx = _context(
            tasks_by_id={},  # task not found
            categories_by_id={},
            creators_by_id={},
            current_user_id=100,
        )

        row = svc._build_row(m, ctx)
        assert row["taskId"] == 999
        assert row["taskName"] == ""


# ---------------------------------------------------------------------------
# get_overview & get_participations (integration of logic)
# ---------------------------------------------------------------------------


class TestGetOverview:
    @pytest.mark.anyio
    async def test_space_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        svc = SpaceMemberParticipatingService(session)

        with pytest.raises(NotFoundError):
            await svc.get_overview(space_id=999, user_id=100)

    @pytest.mark.anyio
    async def test_overview_with_empty_memberships(self):
        session = _mock_session()
        space = SimpleNamespace(id=1, name="Space", deleted_at=None)
        # All execute calls return scalars with empty lists
        session.execute.return_value = _mock_scalars([])
        svc = SpaceMemberParticipatingService(session)
        # Patch space repo to return the space
        svc._space_repo = AsyncMock()
        svc._space_repo.get_by_id.return_value = space

        result = await svc.get_overview(space_id=1, user_id=100)
        assert result["spaceId"] == 1
        assert result["participationCount"] == 0


class TestGetParticipations:
    @pytest.mark.anyio
    async def test_space_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        svc = SpaceMemberParticipatingService(session)

        with pytest.raises(NotFoundError):
            await svc.get_participations(space_id=999, user_id=100)

    @pytest.mark.anyio
    async def test_invalid_approved_filter(self):
        session = _mock_session()
        space = SimpleNamespace(id=1, name="Space", deleted_at=None)
        session.execute.return_value = _mock_scalar(space)
        svc = SpaceMemberParticipatingService(session)

        with pytest.raises(BadRequestError):
            await svc.get_participations(space_id=1, user_id=100, approved="INVALID")

    @pytest.mark.anyio
    async def test_invalid_completion_status_filter(self):
        session = _mock_session()
        space = SimpleNamespace(id=1, name="Space", deleted_at=None)
        session.execute.return_value = _mock_scalar(space)
        svc = SpaceMemberParticipatingService(session)

        with pytest.raises(BadRequestError):
            await svc.get_participations(space_id=1, user_id=100, completion_status="INVALID")

    @pytest.mark.anyio
    async def test_invalid_identity_type_filter(self):
        session = _mock_session()
        space = SimpleNamespace(id=1, name="Space", deleted_at=None)
        session.execute.return_value = _mock_scalar(space)
        svc = SpaceMemberParticipatingService(session)

        with pytest.raises(BadRequestError):
            await svc.get_participations(space_id=1, user_id=100, identity_type="INVALID")

    @pytest.mark.anyio
    async def test_invalid_sort_by(self):
        session = _mock_session()
        space = SimpleNamespace(id=1, name="Space", deleted_at=None)
        session.execute.return_value = _mock_scalar(space)
        svc = SpaceMemberParticipatingService(session)

        with pytest.raises(BadRequestError):
            await svc.get_participations(space_id=1, user_id=100, sort_by="invalid")

    @pytest.mark.anyio
    async def test_invalid_sort_order(self):
        session = _mock_session()
        space = SimpleNamespace(id=1, name="Space", deleted_at=None)
        session.execute.return_value = _mock_scalar(space)
        svc = SpaceMemberParticipatingService(session)

        with pytest.raises(BadRequestError):
            await svc.get_participations(space_id=1, user_id=100, sort_order="invalid")

    @pytest.mark.anyio
    async def test_empty_result(self):
        session = _mock_session()
        space = SimpleNamespace(id=1, name="Space", deleted_at=None)
        session.execute.return_value = _mock_scalars([])
        svc = SpaceMemberParticipatingService(session)
        svc._space_repo = AsyncMock()
        svc._space_repo.get_by_id.return_value = space

        result = await svc.get_participations(space_id=1, user_id=100)
        assert result == []
