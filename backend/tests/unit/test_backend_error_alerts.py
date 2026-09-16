"""An error the backend logs has to reach a person, wherever it was raised.

The failure this exists for was not a 500 in anyone's browser: on 2026-09-16 the
database refused 83 connections in a minute and one of them reached the request
error handler. The rest were background work that logged and carried on.
"""

import logging

import pytest

from app.core import alerting
from app.core.obs import AlertOnError


@pytest.fixture
def sent(monkeypatch):
    """Capture what would go to the channel, without a webhook or a network."""
    posted: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        alerting, "send", lambda title, lines: posted.append((title, lines))
    )
    return posted


@pytest.fixture
def logger():
    log = logging.getLogger("test.backend.errors")
    log.handlers.clear()
    log.addHandler(AlertOnError())
    log.propagate = False
    log.setLevel(logging.DEBUG)
    yield log
    log.handlers.clear()


def test_an_error_logged_in_background_work_is_alerted(logger, sent):
    """The case that went unnoticed: nothing returned a 500, something logged."""
    try:
        raise RuntimeError("connection pool exhausted")
    except RuntimeError:
        logger.exception("hook subscription recovery failed")

    assert len(sent) == 1
    title, lines = sent[0]
    assert "hook subscription recovery failed" in title
    assert any("RuntimeError" in line for line in lines), lines
    assert any("test.backend.errors" in line for line in lines), lines


def test_structlog_shaped_records_report_their_event_and_context(logger, sent):
    """structlog hands the formatter a dict, uvicorn hands it a string; the
    channel must read a sentence out of either."""
    logger.error({"event": "unhandled_error", "path": "/api/topics", "method": "GET"})

    assert len(sent) == 1
    title, lines = sent[0]
    assert "unhandled_error" in title
    assert any("/api/topics" in line for line in lines), lines
    assert any("GET" in line for line in lines), lines


def test_warnings_and_info_are_not_alerts(logger, sent):
    logger.warning("a retry is about to happen")
    logger.info("a turn started")
    assert sent == []


def test_the_alerter_s_own_failures_do_not_feed_back_into_it(sent):
    """Alerting logs when it cannot deliver. If that came back here, one failed
    delivery would drive an unbounded loop."""
    log = logging.getLogger("app.core.alerting")
    log.handlers.clear()
    log.addHandler(AlertOnError())
    log.propagate = False
    try:
        log.error("could not reach the alert webhook")
        assert sent == []
    finally:
        log.handlers.clear()


def test_a_broken_log_call_does_not_break_the_caller(logger, sent):
    """Logging must never raise into the code that logged. A format string with
    no argument for it is the ordinary way that happens."""
    logger.error("stray %s placeholder")  # no args
    # It may or may not produce an alert; what it must not do is raise.
    assert isinstance(sent, list)
