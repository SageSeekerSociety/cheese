"""An error the backend logs has to reach a person, wherever it was raised.

The failure this exists for was not a 500 in anyone's browser: on 2026-09-16 the
database refused 83 connections in a minute and one of them reached the request
error handler. The rest were background work that logged and carried on.
"""

import logging

import httpx
import pytest

from app.core import alerting
from app.core.obs import AlertOnError


@pytest.fixture
def sent(monkeypatch):
    """Capture what would go to the channel, without a webhook or a network."""
    posted: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        alerting,
        "send",
        lambda title, lines, **_: posted.append((title, lines)),
    )
    return posted


@pytest.fixture
def keys(monkeypatch):
    """What each alert said makes two of these the same problem."""
    taken: list[str] = []
    monkeypatch.setattr(
        alerting,
        "send",
        lambda title, lines, *, key=None, **_: taken.append(key or title),
    )
    return taken


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


def _the_server_answered(status: int, **response_kwargs) -> httpx.HTTPStatusError:
    """A real HTTPStatusError, raised the way httpx raises it."""
    request = httpx.Request(
        "POST", "http://device-connection:8082/internal/device-connection/call/exec"
    )
    response = httpx.Response(status, request=request, **response_kwargs)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc
    raise AssertionError(f"{status} did not raise")


def test_an_http_error_reports_what_the_server_answered(logger, sent):
    """`HTTPStatusError` renders as `Client error '409 Conflict' for url '...'`
    and stops there. Fifty of those on 2026-09-16 were the connection owner
    saying `device offline`, and the alert never carried the word."""
    try:
        raise _the_server_answered(
            409, json={"code": 409, "message": "Error: device offline"}
        )
    except httpx.HTTPStatusError:
        logger.exception("cleanup device inventory failed device=a3dc2940aee2")

    assert len(sent) == 1
    _, lines = sent[0]
    assert any("device offline" in line for line in lines), lines
    assert any("409" in line for line in lines), lines


def test_the_answer_arrives_on_one_line(logger, sent):
    """An alert is a list of short lines; a JSON body arrives with newlines."""
    try:
        raise _the_server_answered(500, text='{\n  "message": "boom"\n}')
    except httpx.HTTPStatusError:
        logger.exception("exec failed")

    _, lines = sent[0]
    answered = [line for line in lines if "boom" in line]
    assert answered, lines
    assert "\n" not in answered[0]


def test_a_credential_the_server_echoed_back_is_scrubbed(logger, sent):
    """A 401 can answer with the token it rejected, and an alert reaches a chat
    group — an audience that does not already hold the keys."""
    try:
        raise _the_server_answered(401, text='{"error": "bad token=abcdef123456"}')
    except httpx.HTTPStatusError:
        logger.exception("gateway call failed")

    _, lines = sent[0]
    assert not any("abcdef123456" in line for line in lines), lines
    assert any("401" in line for line in lines), lines


def test_a_long_answer_is_truncated(logger, sent):
    try:
        raise _the_server_answered(502, text="x" * 5000)
    except httpx.HTTPStatusError:
        logger.exception("upstream failed")

    _, lines = sent[0]
    answered = [line for line in lines if "xxx" in line]
    assert answered, lines
    assert len(answered[0]) < 400, len(answered[0])


def test_an_error_with_no_response_is_unchanged(logger, sent):
    """Most errors are not HTTP errors; they must not gain an empty line."""
    try:
        raise RuntimeError("connection pool exhausted")
    except RuntimeError:
        logger.exception("hook subscription recovery failed")

    _, lines = sent[0]
    assert not any("状态：" in line for line in lines), lines
    assert not any("对方回答" in line for line in lines), lines


def test_a_body_that_will_not_read_still_reports_its_status(logger, sent):
    """A streamed response raises on `.text` until it is read. The status is
    still worth saying, and reading must not raise into the log call."""

    class _Unreadable:
        status_code = 502

        @property
        def text(self) -> str:
            raise RuntimeError("response not read")

    error = RuntimeError("upstream refused")
    error.response = _Unreadable()  # type: ignore[attr-defined]
    try:
        raise error
    except RuntimeError:
        logger.exception("exec failed")

    assert len(sent) == 1
    _, lines = sent[0]
    assert any("502" in line for line in lines), lines


def test_an_alert_carries_what_the_exception_said(logger, sent):
    """`异常：RuntimeError` is a message whose entire content is that something
    went wrong. The device had written the reason and it was thrown away —
    every 「executor 找不到」 alert on 2026-09-17 named neither the directory
    nor the room, and reading the logs was the only way to find out."""
    try:
        raise RuntimeError("lstat /room/.cheese: no such file or directory")
    except RuntimeError:
        logger.exception("unhandled_error")

    _, lines = sent[0]
    assert any("no such file" in line for line in lines), lines


def test_an_alert_says_where_it_was_raised(logger, sent):
    """The log file may be gone by the time anyone reads the alert — a
    container is redeployed and `docker logs` starts over."""

    def deep_inside() -> None:
        raise ValueError("nope")

    try:
        deep_inside()
    except ValueError:
        logger.exception("unhandled_error")

    _, lines = sent[0]
    assert any("deep_inside" in line for line in lines), lines


def test_bound_context_is_reported_whatever_it_is_called(logger, sent):
    """A whitelist of field names drops the one field that would have explained
    it, and never says that it did."""
    logger.error(
        {
            "event": "unhandled_error",
            "req": "db7f40382975",
            "trace_id": "execution-9f",
            "room": "cc2958d7",
        }
    )

    _, lines = sent[0]
    body = "\n".join(lines)
    assert "db7f40382975" in body
    assert "execution-9f" in body
    assert "cc2958d7" in body


def test_one_exception_is_one_alert_however_many_layers_log_it(logger, sent):
    """An unhandled request error is logged three times on its way out: the
    request middleware, the catch-all handler, and uvicorn re-raising past
    both. Three alerts for one failure is how a channel teaches people that its
    counts mean nothing."""
    try:
        raise RuntimeError("one failure")
    except RuntimeError:
        logger.exception("request failed")
        logger.exception("unhandled_error")
        logger.exception("Exception in ASGI application")

    assert len(sent) == 1
    assert "request failed" in sent[0][0]


def test_two_failures_that_happen_to_share_a_message_are_both_reported(logger, sent):
    for _ in range(2):
        try:
            raise RuntimeError("one failure")
        except RuntimeError:
            logger.exception("request failed")

    assert len(sent) == 2


def test_the_room_in_a_message_does_not_make_it_a_different_problem(logger, keys):
    """One broken thing on seventy machines is one broken thing. Which rooms is
    in the body; it is not what decides whether this has been reported."""
    logger.error(
        {"event": "pi entry read failed topic=4fa8562d-2593-40d9-98ee-59e67d6477e3"}
    )
    logger.error(
        {"event": "pi entry read failed topic=963aa806-9b8c-492a-b9a3-2895af82918e"}
    )

    assert len(set(keys)) == 1


def test_different_failures_keep_different_keys(logger, keys):
    logger.error({"event": "unhandled_error", "path": "/topics/x/agent/control"})
    logger.error({"event": "unhandled_error", "path": "/projects/y/environment"})

    assert len(set(keys)) == 2
