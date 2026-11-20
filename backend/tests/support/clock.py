"""One "now" for a test and the routes it reads.

A test that dates its rows from its own read of the wall clock and then asks a
route that reads the clock again sees two different days whenever the two reads
straddle UTC midnight, and every row dated "today" falls outside the window.
The product reads "now" for the UTC day in one place, `day_windows.utc_now` (the
admin boards' windows, the profile's year, the gateway's "today"); this pins it,
and a test that dates its rows from `utc_now()` dates them from the same moment.

Pinned to the moment the test starts unless told otherwise, so rows the product
writes with its own timestamps stay on the same day as the pinned one.
"""

from datetime import UTC, datetime

import pytest

from app.domain import day_windows


class PinnedClock:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, at: datetime) -> None:
        self.at = at
        clock = self

        class _Pinned(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.at.astimezone(tz or UTC)

        monkeypatch.setattr(day_windows, "datetime", _Pinned)

    def move_to(self, at: datetime) -> None:
        """Every later read of `utc_now()` returns ``at``."""
        self.at = at
