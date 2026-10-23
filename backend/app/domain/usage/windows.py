"""A plan's time windows: when a round starts, when it runs out, how full it is.

A windowed plan lets a team spend up to a cap inside each of its time windows:
an hours window starts at the team's first call and resets that many hours
later; a week or month window resets every Monday or every first of the month.
A monthly plan issues no window, so nothing here applies to it (``payers.Terms``
tells the two apart).

The day boundary is the platform's, Asia/Shanghai — "this month" and "this week"
mean that month and week to the people using it — and it is written down once,
here. Everything below is the clock alone: no session, no row. A window's row in
``plan_window_use`` is read by the ledger (``ledger_queries.LedgerQueries``).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain.usage.models import PlanWindowUse

# Where a month starts and ends for the people using the platform.
_TZ = ZoneInfo("Asia/Shanghai")


def month_of(moment: datetime) -> date:
    local = moment.astimezone(_TZ)
    return date(local.year, local.month, 1)


def month_start(month: date) -> datetime:
    """The first moment of ``month``: where 「本月」 starts being counted.

    One definition, because a month is read in more than one place — the plan's
    pack lapses at `month_end`, and a per-rule figure counts from here — and two
    copies of 「这个月从哪一刻起」 is how those two come to disagree.
    """
    return datetime(month.year, month.month, 1, tzinfo=_TZ)


def month_end(month: date) -> datetime:
    """When the month's plan pack lapses and the next month's becomes available."""
    following = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return month_start(following)


def week_of(moment: datetime) -> datetime:
    """The Monday midnight that starts the week ``moment`` falls in."""
    local = moment.astimezone(_TZ)
    monday = local.date() - timedelta(days=local.weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=_TZ)


@dataclass(frozen=True)
class Window:
    """One of a plan's time windows: at most ``credits`` inside it. An hours
    window starts at a team's first call; a ``calendar`` one is a week or a
    month."""

    credits: float
    hours: float | None = None
    calendar: str | None = None

    @classmethod
    def of(cls, raw: Mapping) -> "Window":
        if raw.get("calendar") is not None:
            return cls(credits=float(raw["credits"]), calendar=str(raw["calendar"]))
        return cls(credits=float(raw["credits"]), hours=float(raw["hours"]))

    @property
    def key(self) -> str:
        """The window's row in ``plan_window_use``."""
        return self.calendar or f"{self.hours:g}h"

    def fresh_since(self, now: datetime) -> datetime:
        """A round that started before this has run out."""
        if self.calendar == "week":
            return week_of(now)
        if self.calendar == "month":
            return datetime.combine(month_of(now), datetime.min.time(), tzinfo=_TZ)
        assert self.hours is not None
        return now - timedelta(hours=self.hours)

    def start(self, now: datetime) -> datetime:
        """When a round that starts with a call at ``now`` began."""
        return now if self.calendar is None else self.fresh_since(now)

    def resets_at(self, started_at: datetime) -> datetime:
        if self.calendar == "week":
            return started_at + timedelta(days=7)
        if self.calendar == "month":
            return month_end(month_of(started_at))
        assert self.hours is not None
        return started_at + timedelta(hours=self.hours)


@dataclass(frozen=True)
class WindowUse:
    """How full one window is now. ``resets_at`` is None for an hours window no
    call has started yet."""

    window: Window
    credits_used: float
    resets_at: datetime | None

    @property
    def full(self) -> bool:
        return self.credits_used >= self.window.credits

    @property
    def room(self) -> float:
        return max(0.0, self.window.credits - self.credits_used)


def _window_use(window: Window, row: PlanWindowUse | None, now: datetime) -> WindowUse:
    if row is not None and row.started_at >= window.fresh_since(now):
        return WindowUse(window, row.credits_used, window.resets_at(row.started_at))
    if window.calendar is None:
        return WindowUse(window, 0.0, None)
    return WindowUse(window, 0.0, window.resets_at(window.start(now)))
