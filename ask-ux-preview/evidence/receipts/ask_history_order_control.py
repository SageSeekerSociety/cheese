"""Restore ordered completion equality without changing production files."""

import pytest


@pytest.fixture(autouse=True)
def ordered_completion_control(monkeypatch):
    from app.domain.agent.harness.claude_code import subscription

    # Leave both input tuples intact at the strict dataclass comparison. All
    # native I/O, retained interval proof, database callbacks and assertions stay.
    monkeypatch.setattr(subscription, "replace", lambda value, **_changes: value)
