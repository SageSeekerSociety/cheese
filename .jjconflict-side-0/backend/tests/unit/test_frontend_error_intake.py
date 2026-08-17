"""Frontend-error intake guard rails: fingerprint identity, dedup window, and
the per-project rate cap (a render-loop error must not flood blocks)."""

from app.domain.frontend_log import (
    DEDUP_WINDOW_S,
    MAX_BLOCKS_PER_WINDOW,
    FrontendErrorIn,
    FrontendErrorIntake,
    event_content,
    event_meta,
    fingerprint,
)


def test_fingerprint_ignores_deep_stack_but_not_message() -> None:
    a = FrontendErrorIn(message="boom", stack="Error: boom\n  at a.js:1\n  at b.js:2")
    b = FrontendErrorIn(message="boom", stack="Error: boom\n  at a.js:1\n  at c.js:9")
    c = FrontendErrorIn(message="other", stack="Error: boom\n  at a.js:1")
    assert fingerprint(a) == fingerprint(b)
    assert fingerprint(a) != fingerprint(c)


def test_duplicate_collapses_within_window() -> None:
    intake = FrontendErrorIntake()
    assert intake.admit("p", "fp", now=1000.0)
    assert not intake.admit("p", "fp", now=1001.0)
    assert intake.admit("p", "fp", now=1000.0 + DEDUP_WINDOW_S + 1)


def test_rate_cap_is_per_project() -> None:
    intake = FrontendErrorIntake()
    for i in range(MAX_BLOCKS_PER_WINDOW):
        assert intake.admit("p", f"fp{i}", now=1000.0 + i)
    assert not intake.admit("p", "fresh", now=1000.0 + MAX_BLOCKS_PER_WINDOW)
    assert intake.admit("q", "fresh", now=1000.0)


def test_event_rendering() -> None:
    err = FrontendErrorIn(message="boom", stack="s", source="a.js:1", page="/project/x")
    assert "🐞" in event_content(err)
    assert "/project/x" in event_content(err)
    meta = event_meta(err)
    assert meta["event_type"] == "frontend_error"
    assert meta["stack"] == "s"
    assert meta["source"] == "a.js:1"
