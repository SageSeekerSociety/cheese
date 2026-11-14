"""Frontend-error intake guard rails: fingerprint identity, dedup window, and
the per-project rate cap (a render-loop error must not flood blocks)."""

from app.domain.frontend_log import (
    DEDUP_WINDOW_S,
    MAX_ALERTS_PER_WINDOW,
    MAX_BLOCKS_PER_WINDOW,
    FrontendErrorIn,
    FrontendErrorIntake,
    event_content,
    event_meta,
    fingerprint,
    worth_alerting,
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
    assert "/project/x" in event_content(err)
    # 这是不是一条前端报错，读码，不读开头那个字符。
    meta = event_meta(err)
    assert meta["event_type"] == "frontend_error"
    assert meta["stack"] == "s"
    assert meta["source"] == "a.js:1"


def test_alerts_stop_across_projects_while_reports_are_still_admitted() -> None:
    """Anyone can report; past the window's alerts the admins hear nothing more,
    whichever projects the reports name, until the window has moved on."""
    intake = FrontendErrorIntake()
    now = 1000.0
    for i in range(MAX_ALERTS_PER_WINDOW):
        assert intake.admit(f"project-{i}", "fp", now=now)
        assert intake.may_alert(now=now)
    assert intake.admit("project-new", "fp", now=now)
    assert not intake.may_alert(now=now)
    assert intake.may_alert(now=now + DEDUP_WINDOW_S + 1)


def test_an_error_a_browser_extension_threw_is_not_alerted() -> None:
    """An extension's script runs inside the page and its rejections reach the
    page's handler; the stack's first frame says whose code it was."""
    extension = FrontendErrorIn(
        message="Unhandled rejection: func sseError not found",
        stack="Error: func sseError not found\n"
        "    at Object.<anonymous> (chrome-extension://abc/inpage.js:253:4574)\n"
        "    at O (chrome-extension://abc/inpage.js:252:16359)",
    )
    firefox = FrontendErrorIn(message="x", stack="f@moz-extension://abc/content.js:1:2")
    ours_through_one = FrontendErrorIn(
        message="TypeError: x is undefined",
        stack="TypeError: x is undefined\n"
        "    at pc (https://okcheese.com/assets/vue-C-oHDLPt.js:3:51001)\n"
        "    at chrome-extension://abc/inpage.js:1:1",
    )
    assert not worth_alerting(extension, "https://okcheese.com")
    assert not worth_alerting(firefox, "https://okcheese.com")
    assert worth_alerting(ours_through_one, "https://okcheese.com")
    assert worth_alerting(FrontendErrorIn(message="boom"), "https://okcheese.com")
