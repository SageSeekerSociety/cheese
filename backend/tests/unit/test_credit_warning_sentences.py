"""A windowed plan's warning names its window, not 「这个窗口」."""

from datetime import date, datetime

import pytest

from app.domain.usage import alerts
from app.domain.usage.payers import Terms
from app.domain.usage.windows import _TZ, Window, WindowUse


def _terms(*windows: Window) -> Terms:
    return Terms(key="pro", credits_per_period=None, windows=windows)


def _use(window: Window, used: float, resets_at: datetime) -> WindowUse:
    return WindowUse(window=window, credits_used=used, resets_at=resets_at)


@pytest.mark.parametrize(
    ("window", "expected"),
    [
        (Window(credits=1000, calendar="week"), "creditWarningTeamWeek"),
        (Window(credits=1000, calendar="month"), "creditWarningTeamMonth"),
        (Window(credits=1000, hours=5), "creditWarningTeamHours"),
    ],
)
def test_a_window_is_said_by_its_name(window, expected):
    reset = datetime(2026, 11, 2, 0, 0, tzinfo=_TZ)
    got = alerts._team_warning(
        _terms(window), None, date(2026, 11, 1), [_use(window, 900, reset)]
    )
    assert got is not None
    assert got.sentence.key == expected
    assert "900" not in str(got.sentence)  # the sentence says what is left
    assert "100" in str(got.sentence)


def test_the_fullest_window_is_the_one_said():
    week = Window(credits=1000, calendar="week")
    hours = Window(credits=100, hours=5)
    reset = datetime(2026, 11, 2, 0, 0, tzinfo=_TZ)
    got = alerts._team_warning(
        _terms(week, hours),
        None,
        date(2026, 11, 1),
        [_use(week, 100, reset), _use(hours, 95, reset)],
    )
    assert got is not None
    assert got.sentence.key == "creditWarningTeamHours"
    assert got.total == 100


def test_a_raised_limit_is_a_different_crossing():
    """The marker carries the limit, so a window given more credits is news
    again instead of staying silent because it was crossed at the old one."""
    reset = datetime(2026, 11, 2, 0, 0, tzinfo=_TZ)
    week = Window(credits=1000, calendar="week")
    bigger = Window(credits=2000, calendar="week")
    first = alerts._team_warning(
        _terms(week), None, date(2026, 11, 1), [_use(week, 850, reset)]
    )
    raised = alerts._team_warning(
        _terms(bigger), None, date(2026, 11, 1), [_use(bigger, 1700, reset)]
    )
    assert first is not None and raised is not None
    assert first.marker != raised.marker


def test_a_window_with_room_is_not_a_warning():
    week = Window(credits=1000, calendar="week")
    reset = datetime(2026, 11, 2, 0, 0, tzinfo=_TZ)
    assert (
        alerts._team_warning(
            _terms(week), None, date(2026, 11, 1), [_use(week, 700, reset)]
        )
        is None
    )
