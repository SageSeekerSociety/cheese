"""Unit tests for helper/static methods in analytics_view_service.

Covers the static parsing, normalization, bucketing, and utility methods
without requiring DB access or complex context setup.
"""

from collections import Counter
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.core.errors import BadRequestError
from app.domain.space.analytics_view_service import SpaceAnalyticsViewService

NOW = datetime(2025, 6, 1, 12, 0, 0)


def _svc():
    return SpaceAnalyticsViewService(AsyncMock())


# ---------------------------------------------------------------------------
# _to_timestamp_ms
# ---------------------------------------------------------------------------


class TestToTimestampMs:
    def test_none(self):
        assert SpaceAnalyticsViewService._to_timestamp_ms(None) is None

    def test_naive_datetime(self):
        result = SpaceAnalyticsViewService._to_timestamp_ms(NOW)
        assert isinstance(result, int)
        assert result > 0

    def test_aware_datetime(self):
        dt = NOW.replace(tzinfo=UTC)
        result = SpaceAnalyticsViewService._to_timestamp_ms(dt)
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# _safe_ratio
# ---------------------------------------------------------------------------


class TestSafeRatio:
    def test_zero_denominator(self):
        assert SpaceAnalyticsViewService._safe_ratio(5, 0) == 0.0

    def test_negative_denominator(self):
        assert SpaceAnalyticsViewService._safe_ratio(5, -1) == 0.0

    def test_normal(self):
        result = SpaceAnalyticsViewService._safe_ratio(3, 4)
        assert result == 0.75


# ---------------------------------------------------------------------------
# _resolve_window
# ---------------------------------------------------------------------------


class TestResolveWindow:
    def test_both_none(self):
        from_dt, to_dt = SpaceAnalyticsViewService._resolve_window(None, None)
        assert from_dt < to_dt

    def test_both_provided(self):
        from_ts = int(NOW.replace(tzinfo=UTC).timestamp() * 1000)
        to_ts = int((NOW + timedelta(days=30)).replace(tzinfo=UTC).timestamp() * 1000)
        from_dt, to_dt = SpaceAnalyticsViewService._resolve_window(from_ts, to_ts)
        assert from_dt < to_dt

    def test_from_only(self):
        from_ts = int(NOW.replace(tzinfo=UTC).timestamp() * 1000)
        from_dt, to_dt = SpaceAnalyticsViewService._resolve_window(from_ts, None)
        assert from_dt < to_dt

    def test_to_only(self):
        to_ts = int(NOW.replace(tzinfo=UTC).timestamp() * 1000)
        from_dt, to_dt = SpaceAnalyticsViewService._resolve_window(None, to_ts)
        assert from_dt < to_dt

    def test_from_after_to_raises(self):
        to_ts = int(NOW.replace(tzinfo=UTC).timestamp() * 1000)
        from_ts = int((NOW + timedelta(days=1)).replace(tzinfo=UTC).timestamp() * 1000)
        with pytest.raises(BadRequestError, match=r"from.*<=.*to"):
            SpaceAnalyticsViewService._resolve_window(from_ts, to_ts)


# ---------------------------------------------------------------------------
# _parse_approved
# ---------------------------------------------------------------------------


class TestParseApproved:
    def test_none(self):
        assert SpaceAnalyticsViewService._parse_approved(None) is None

    def test_valid_approved(self):
        assert SpaceAnalyticsViewService._parse_approved("APPROVED") == 0

    def test_valid_disapproved(self):
        assert SpaceAnalyticsViewService._parse_approved("DISAPPROVED") == 1

    def test_valid_none_str(self):
        assert SpaceAnalyticsViewService._parse_approved("NONE") == 2

    def test_invalid(self):
        with pytest.raises(BadRequestError):
            SpaceAnalyticsViewService._parse_approved("INVALID")


# ---------------------------------------------------------------------------
# _parse_approved_optional
# ---------------------------------------------------------------------------


class TestParseApprovedOptional:
    def test_none(self):
        assert SpaceAnalyticsViewService._parse_approved_optional(None) is None

    def test_empty(self):
        assert SpaceAnalyticsViewService._parse_approved_optional("") is None

    def test_valid(self):
        assert SpaceAnalyticsViewService._parse_approved_optional("APPROVED") == 0

    def test_invalid(self):
        with pytest.raises(BadRequestError):
            SpaceAnalyticsViewService._parse_approved_optional("BAD")


# ---------------------------------------------------------------------------
# _normalize_completion_status
# ---------------------------------------------------------------------------


class TestNormalizeCompletionStatus:
    def test_none(self):
        assert SpaceAnalyticsViewService._normalize_completion_status(None) is None

    def test_empty(self):
        assert SpaceAnalyticsViewService._normalize_completion_status("") is None

    def test_valid(self):
        assert SpaceAnalyticsViewService._normalize_completion_status("SUCCESS") == "SUCCESS"

    def test_case_insensitive(self):
        assert SpaceAnalyticsViewService._normalize_completion_status("success") == "SUCCESS"

    def test_invalid(self):
        with pytest.raises(BadRequestError):
            SpaceAnalyticsViewService._normalize_completion_status("BAD")


# ---------------------------------------------------------------------------
# _normalize_real_name_filter
# ---------------------------------------------------------------------------


class TestNormalizeRealNameFilter:
    def test_all(self):
        assert SpaceAnalyticsViewService._normalize_real_name_filter("all") == "all"

    def test_with(self):
        assert SpaceAnalyticsViewService._normalize_real_name_filter("with") == "with"

    def test_without(self):
        assert SpaceAnalyticsViewService._normalize_real_name_filter("without") == "without"

    def test_invalid(self):
        with pytest.raises(BadRequestError):
            SpaceAnalyticsViewService._normalize_real_name_filter("invalid")


# ---------------------------------------------------------------------------
# _normalize_group_by
# ---------------------------------------------------------------------------


class TestNormalizeGroupBy:
    def test_day(self):
        assert SpaceAnalyticsViewService._normalize_group_by("day") == "day"

    def test_week(self):
        assert SpaceAnalyticsViewService._normalize_group_by("week") == "week"

    def test_month(self):
        assert SpaceAnalyticsViewService._normalize_group_by("month") == "month"

    def test_invalid(self):
        with pytest.raises(BadRequestError):
            SpaceAnalyticsViewService._normalize_group_by("year")


# ---------------------------------------------------------------------------
# _normalize_publisher_sort_by
# ---------------------------------------------------------------------------


class TestNormalizePublisherSortBy:
    def test_task_count(self):
        assert SpaceAnalyticsViewService._normalize_publisher_sort_by("taskCount") == "taskCount"

    def test_participant_count(self):
        assert (
            SpaceAnalyticsViewService._normalize_publisher_sort_by("participantCount")
            == "participantCount"
        )

    def test_invalid(self):
        with pytest.raises(BadRequestError):
            SpaceAnalyticsViewService._normalize_publisher_sort_by("bad")


# ---------------------------------------------------------------------------
# _normalize_task_sort_by
# ---------------------------------------------------------------------------


class TestNormalizeTaskSortBy:
    def test_created_at(self):
        assert SpaceAnalyticsViewService._normalize_task_sort_by("createdAt") == "createdAt"

    def test_deadline(self):
        assert SpaceAnalyticsViewService._normalize_task_sort_by("deadline") == "deadline"

    def test_invalid(self):
        with pytest.raises(BadRequestError):
            SpaceAnalyticsViewService._normalize_task_sort_by("bad")


# ---------------------------------------------------------------------------
# _normalize_sort_order
# ---------------------------------------------------------------------------


class TestNormalizeSortOrder:
    def test_asc(self):
        assert SpaceAnalyticsViewService._normalize_sort_order("asc") == "asc"

    def test_desc(self):
        assert SpaceAnalyticsViewService._normalize_sort_order("desc") == "desc"

    def test_invalid(self):
        with pytest.raises(BadRequestError):
            SpaceAnalyticsViewService._normalize_sort_order("bad")


# ---------------------------------------------------------------------------
# _bucket_key
# ---------------------------------------------------------------------------


class TestBucketKey:
    def test_day(self):
        dt = datetime(2025, 6, 15, 14, 30, 45)
        result = SpaceAnalyticsViewService._bucket_key(dt, "day")
        assert result == datetime(2025, 6, 15, 0, 0, 0)

    def test_week(self):
        # 2025-06-15 is a Sunday (weekday=6) -> Monday = 2025-06-09
        dt = datetime(2025, 6, 15, 14, 30, 45)
        result = SpaceAnalyticsViewService._bucket_key(dt, "week")
        assert result.weekday() == 0  # Monday

    def test_month(self):
        dt = datetime(2025, 6, 15, 14, 30, 45)
        result = SpaceAnalyticsViewService._bucket_key(dt, "month")
        assert result == datetime(2025, 6, 1, 0, 0, 0)

    def test_aware_datetime(self):
        dt = datetime(2025, 6, 15, 14, 30, 45, tzinfo=UTC)
        result = SpaceAnalyticsViewService._bucket_key(dt, "day")
        assert result.tzinfo is None


# ---------------------------------------------------------------------------
# _bucketize
# ---------------------------------------------------------------------------


class TestBucketize:
    def test_empty_values(self):
        svc = _svc()
        from_dt = datetime(2025, 6, 1)
        to_dt = datetime(2025, 6, 30)
        result = svc._bucketize([], from_dt=from_dt, to_dt=to_dt, group_by="day")
        assert result == []

    def test_with_values(self):
        svc = _svc()
        from_dt = datetime(2025, 6, 1)
        to_dt = datetime(2025, 6, 30)
        values = [
            datetime(2025, 6, 5, 10, 0),
            datetime(2025, 6, 5, 14, 0),
            datetime(2025, 6, 10, 8, 0),
        ]
        result = svc._bucketize(values, from_dt=from_dt, to_dt=to_dt, group_by="day")
        assert len(result) == 2
        assert result[0]["count"] == 2
        assert result[1]["count"] == 1

    def test_filters_out_of_range(self):
        svc = _svc()
        from_dt = datetime(2025, 6, 1)
        to_dt = datetime(2025, 6, 30)
        values = [datetime(2025, 5, 15), datetime(2025, 7, 1)]
        result = svc._bucketize(values, from_dt=from_dt, to_dt=to_dt, group_by="day")
        assert result == []

    def test_none_values_ignored(self):
        svc = _svc()
        from_dt = datetime(2025, 6, 1)
        to_dt = datetime(2025, 6, 30)
        values = [None, datetime(2025, 6, 5), None]
        result = svc._bucketize(values, from_dt=from_dt, to_dt=to_dt, group_by="day")
        assert len(result) == 1
        assert result[0]["count"] == 1

    def test_week_grouping(self):
        svc = _svc()
        from_dt = datetime(2025, 6, 1)
        to_dt = datetime(2025, 6, 30)
        values = [datetime(2025, 6, 2), datetime(2025, 6, 4)]  # same week
        result = svc._bucketize(values, from_dt=from_dt, to_dt=to_dt, group_by="week")
        assert len(result) == 1
        assert result[0]["count"] == 2


# ---------------------------------------------------------------------------
# _build_distribution
# ---------------------------------------------------------------------------


class TestBuildDistribution:
    def test_empty_counter(self):
        result = SpaceAnalyticsViewService._build_distribution("test", Counter())
        assert result["name"] == "test"
        assert result["type"] == "DISCRETE"
        assert result["items"] == []

    def test_with_values(self):
        counter = Counter({"A": 3, "B": 1, "C": 1})
        result = SpaceAnalyticsViewService._build_distribution("status", counter)
        assert result["name"] == "status"
        assert len(result["items"]) == 3
        # Most common first
        assert result["items"][0]["label"] == "A"
        assert result["items"][0]["count"] == 3
        assert result["items"][0]["percentage"] == 60.0
