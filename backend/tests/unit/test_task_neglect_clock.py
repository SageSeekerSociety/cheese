"""The neglect clock: who is warned when, and when a task is closed.

`presentation.neglect` is pure, so the whole table is checked here without a
database: a task waiting on someone is warned after 3 days and closed after 7;
any other task is warned after 7 and closed after 14; the platform moving a
task along, or a task already done, is on no clock.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.room_task import presentation
from app.domain.room_task.presentation import Column, Presentation

pytestmark = pytest.mark.anyio

_NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)


def _shown(column: Column, phrase: str) -> Presentation:
    return Presentation(column=column, phrase=phrase)


def _clock(shown: Presentation, quiet_days: float):
    return presentation.neglect(shown, last_activity=_NOW - timedelta(days=quiet_days))


@pytest.mark.parametrize(
    ("phrase", "warn_at", "close_at"),
    [
        ("awaiting_review", 3, 7),
        ("awaiting_answer", 3, 7),
        ("running", 7, 14),
        ("waiting", 7, 14),
        ("bounced", 7, 14),
        ("blocked", 7, 14),
    ],
)
def test_each_kind_of_task_has_its_own_clock(phrase, warn_at, close_at):
    column = (
        Column.needs_you
        if phrase in {"awaiting_review", "awaiting_answer", "bounced", "blocked"}
        else Column.building
    )
    shown = _shown(column, phrase)
    assert not _clock(shown, warn_at - 0.1).dormant(_NOW)
    assert _clock(shown, warn_at).dormant(_NOW)
    assert not _clock(shown, close_at - 0.1).due(_NOW)
    assert _clock(shown, close_at).due(_NOW)


def test_waiting_on_someone_is_marked():
    assert _clock(_shown(Column.needs_you, "awaiting_review"), 1).waiting_on_someone
    assert not _clock(_shown(Column.building, "running"), 1).waiting_on_someone


@pytest.mark.parametrize(
    "shown",
    [
        _shown(Column.done, "accepted"),
        _shown(Column.done, "closed"),
        _shown(Column.archived, "archived"),
        _shown(Column.delivering, "gate_running"),
        _shown(Column.delivering, "awaiting_checks"),
        _shown(Column.delivering, "fixing_checks"),
        _shown(Column.delivering, "resolving_conflict"),
        _shown(Column.delivering, "updating_branch"),
        _shown(Column.delivering, "deploying"),
    ],
)
def test_a_finished_task_or_one_the_platform_is_moving_is_on_no_clock(shown):
    assert _clock(shown, 100) is None


def test_the_times_are_counted_from_the_last_sign_of_life():
    since = _NOW - timedelta(days=2)
    clock = presentation.neglect(
        _shown(Column.needs_you, "awaiting_review"), last_activity=since
    )
    assert clock is not None
    assert clock.since == since
    assert clock.dormant_at == since + timedelta(days=3)
    assert clock.close_at == since + timedelta(days=7)


async def test_the_kill_switch_stops_the_close_before_anything_is_read(monkeypatch):
    from unittest.mock import Mock

    from app.core.config import settings
    from app.domain.task_quiet import neglect

    monkeypatch.setattr(settings, "task_neglect_auto_close", False)
    sessions = Mock(side_effect=AssertionError("the sweep opened a session"))
    closed = await neglect.close_neglected(
        sessions, chat=Mock(), settle_card=None, task_teammate=None
    )
    assert closed == 0


def test_a_row_carries_neglect_only_once_the_task_is_dormant():
    from app.api.room_task_rows import _neglect_json

    shown = _shown(Column.needs_you, "awaiting_review")
    early = _clock(shown, 1)
    late = _clock(shown, 4)
    assert _neglect_json(early, _NOW, running=False) is None
    row = _neglect_json(late, _NOW, running=False)
    assert row is not None
    assert row["waiting_on_someone"] is True
    assert row["close_at"] == (_NOW - timedelta(days=4) + timedelta(days=7)).isoformat()
    assert _neglect_json(late, _NOW, running=True) is None
    assert _neglect_json(None, _NOW, running=False) is None
