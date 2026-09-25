"""A routine fires at the moment the person named, in their own time zone."""

from datetime import UTC, datetime

import pytest

from app.core.errors import ValidationError
from app.domain.routine.schedule import next_after, normalize


def test_weekly_monday_nine_in_shanghai_is_01_utc():
    spec = normalize(
        {"freq": "weekly", "weekdays": [0], "time": "09:00"}, "Asia/Shanghai"
    )
    # Friday 2026-09-25 18:00 UTC → next Monday 09:00 +08 = 01:00 UTC.
    got = next_after(spec, "Asia/Shanghai", datetime(2026, 9, 25, 18, 0, tzinfo=UTC))
    assert got == datetime(2026, 9, 28, 1, 0, tzinfo=UTC)


def test_a_moment_exactly_now_is_not_next():
    spec = normalize({"freq": "daily", "time": "09:00"}, "UTC")
    at = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
    assert next_after(spec, "UTC", at) == datetime(2026, 9, 26, 9, 0, tzinfo=UTC)


def test_monthly_31st_falls_on_the_last_day_of_a_short_month():
    spec = normalize({"freq": "monthly", "day": 31, "time": "08:00"}, "UTC")
    got = next_after(spec, "UTC", datetime(2027, 2, 1, tzinfo=UTC))
    assert got == datetime(2027, 2, 28, 8, 0, tzinfo=UTC)


def test_daily_keeps_local_time_across_a_dst_change():
    spec = normalize({"freq": "daily", "time": "09:00"}, "America/New_York")
    before = next_after(
        spec, "America/New_York", datetime(2026, 10, 31, 20, tzinfo=UTC)
    )
    after = next_after(spec, "America/New_York", datetime(2026, 11, 1, 20, tzinfo=UTC))
    assert before.hour == 13  # EDT, UTC-4
    assert after.hour == 14  # EST, UTC-5


def test_hourly_fires_at_the_named_minute():
    spec = normalize({"freq": "hourly", "minute": 15}, "UTC")
    got = next_after(spec, "UTC", datetime(2026, 9, 25, 10, 20, tzinfo=UTC))
    assert got == datetime(2026, 9, 25, 11, 15, tzinfo=UTC)


@pytest.mark.parametrize(
    "spec",
    [
        {"freq": "weekly", "time": "09:00"},
        {"freq": "daily", "time": "9点"},
        {"freq": "sometimes"},
        {"freq": "monthly", "day": 32, "time": "09:00"},
    ],
)
def test_an_ambiguous_schedule_is_refused(spec):
    with pytest.raises(ValidationError):
        normalize(spec, "Asia/Shanghai")


def test_an_unknown_time_zone_is_refused():
    with pytest.raises(ValidationError):
        normalize({"freq": "daily", "time": "09:00"}, "Mars/Olympus")
