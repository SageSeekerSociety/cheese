"""What the logs must and must not say.

Both of these come from one incident: a chat WebSocket whose path missed the
route by one `/api`. The request log printed the full session token, and the
refusal was an INFO line with no path — so the report that came back was 网络不
稳定 rather than a routing bug, and a live credential sat in `docker logs`.
"""

import logging
import sys
from typing import Any, cast

import pytest
import uvicorn
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.obs import RedactSecrets, configure_logging, scrub_secrets


def test_the_filter_is_actually_installed_by_the_real_setup():
    """The wiring, not the mechanism.

    The first version of this fix put a working filter on a `configure_logging`
    that nothing called — the app uses a different module — and every test still
    passed, because they exercised the class directly. Tokens kept being logged.
    So: run the setup the app runs, then look at the handler it left behind, and
    push a record through it the way uvicorn does.
    """
    configure_logging()
    handlers = logging.getLogger().handlers
    assert handlers, "configure_logging left no handler"
    assert any(isinstance(f, RedactSecrets) for h in handlers for f in h.filters), (
        "the redaction filter is not on the handler the app installs"
    )

    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "WebSocket %s"',
        args=("1.2.3.4:5", "/rooms/live?token=LIVE-SESSION-TOKEN"),
        exc_info=None,
    )
    for h in handlers:
        for f in h.filters:
            f.filter(record)
    assert "LIVE-SESSION-TOKEN" not in record.getMessage()


def test_an_exception_line_does_not_carry_the_frame_it_came_from():
    """A traceback must cost a traceback's worth of bytes.

    The renderer decides this, and its default decides it badly: given rich —
    which arrives transitively, not by our choosing — it dumps every frame's
    locals. One unhandled exception in a route then serialises the whole
    resolved dependency tree, synchronously, on the event loop, and the process
    stops answering anything at all (dev, 2026-08-20).

    So: raise from a frame holding something big, push it through the handler
    the app really installs, and read what comes out.
    """
    configure_logging()
    handler = logging.getLogger().handlers[0]

    def failing_frame():
        big_local = ["x" * 200 for _ in range(500)]  # noqa: F841 — the point
        raise ValueError("upstream said no")

    try:
        failing_frame()
    except ValueError:
        record = logging.LogRecord(
            name="app.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="request failed",
            args=(),
            exc_info=sys.exc_info(),
        )
        rendered = handler.format(record)

    assert "ValueError" in rendered, "the exception itself must still be readable"
    assert "failing_frame" in rendered, "the frame it came from must still be named"
    assert "big_local" not in rendered, "frame locals are being dumped into the log"
    assert len(rendered) < 4000, f"one traceback rendered {len(rendered)} bytes"


def test_a_session_token_never_reaches_the_log():
    """Browsers cannot set a header on a WebSocket, so every WS carries the token
    in its query string — and the access log prints whole URLs."""
    url = "/rooms/live?token=eyJhbGciOiJIUzI1NiJ9.body.signature"

    scrubbed = scrub_secrets(url)

    assert "eyJhbGciOiJIUzI1NiJ9" not in scrubbed
    assert scrubbed == "/rooms/live?token=***"


@pytest.mark.parametrize(
    "raw",
    [
        "a=1&password=hunter2",
        "GET /x?api_key=abc123",
        "/device?code=ABCD-EFGH",
    ],
)
def test_other_credential_shapes_are_scrubbed(raw):
    assert "***" in scrub_secrets(raw)


# The shapes a TRACEBACK produces, which is a different set from the ones a URL
# produces — reprs quote their values, `Bearer` carries no key name at all, and a
# DSN hides the password between a colon and an `@`. Every line below leaked past
# the query-string-only pattern this filter started as. Asserting on the SECRET's
# absence, not on `***` being present: a partial match can do both at once.
@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("Settings(anthropic_auth_token='sk-ant-api03-SECRETVALUE')", "SECRETVALUE"),
        ('ValueError: bad {"token": "ghp_SECRETVALUE"}', "SECRETVALUE"),
        ("headers={'Authorization': 'Bearer ghp_SECRETVALUE'}", "SECRETVALUE"),
        ("DSN postgresql://user:SECRETPW@host/db", "SECRETPW"),
        ("call(token='ghp_SECRETVALUE', x=1)", "SECRETVALUE"),
        # No key name anywhere — only the value's own shape gives it away. This
        # is the case that does not depend on us having guessed the field name.
        ("upstream said sk-ant-api03-SECRETVALUE is invalid", "SECRETVALUE"),
        ("cookie jar: eyJhbGciOi.eyJzdWIiOi.SECRETSIG", "SECRETSIG"),
    ],
)
def test_credentials_in_a_traceback_are_scrubbed(raw, secret):
    assert secret not in scrub_secrets(raw)


@pytest.mark.parametrize(
    "raw",
    [
        # `code` matched as a tail would scrub this out of every traceback that
        # carries one, and the status is often the whole diagnosis.
        "status_code=500 upstream=timeout",
        "GET /api/topics/abc/blocks 200",
        "rows=3 elapsed_ms=12",
        # A bare `code` is not a credential. It was, and the alert channel then
        # carried the one field that mattered as `***`.
        '{"code":409,"message":"Error: device offline"}',
        "device link closed device=abc123 code=1000 after=42.0s",
    ],
)
def test_ordinary_diagnostics_survive_the_filter(raw):
    """Over-scrubbing costs the thing these reports exist to provide."""
    assert scrub_secrets(raw) == raw


def test_scrubbing_a_format_string_does_not_break_the_log_call():
    """The filter runs on `record.msg`, which for a lazy log call is the FORMAT
    STRING — so removing a placeholder there is not cosmetic. `code=%s` used to
    become `code=***`, leaving one placeholder fewer than arguments, and the
    logging call raised TypeError back into whatever was being logged. A close
    code logged on a WebSocket teardown took three connector tests down that way.
    """
    log = logging.getLogger("test.log.hygiene.format")
    log.handlers.clear()
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            # getMessage() is where the mismatch raises, and it is what every
            # real handler calls.
            records.append(record)
            record.getMessage()

    handler = Capture()
    handler.addFilter(RedactSecrets())
    log.addHandler(handler)
    log.propagate = False
    log.setLevel(logging.INFO)
    try:
        log.info(
            "device link closed device=%s code=%s after=%.1fs", "abc123", 1000, 42.0
        )
        assert records[0].getMessage() == (
            "device link closed device=abc123 code=1000 after=42.0s"
        )
    finally:
        log.handlers.clear()


def test_the_filter_covers_records_from_other_libraries():
    """The leak came from uvicorn's logger — code we never call — so the filter
    has to sit on the handler, not on our own log statements."""
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "WebSocket %s" 403',
        args=("1.2.3.4:5", "/rooms/live?token=SECRETVALUE"),
        exc_info=None,
    )

    RedactSecrets().filter(record)

    assert "SECRETVALUE" not in record.getMessage()


def test_the_request_line_names_the_user_client_and_site(client, caplog, monkeypatch):
    """The "req" line must say WHO, from where, and to which site.

    From the 2026-08-10 account-link incident (#222): every request logged the
    edge proxy's address as its peer, and the request line carried no user id —
    so telling two people's browsers apart took correlating adjacent requests
    by hand. The line carries the authenticated user, the client address the
    server resolved through its trusted proxies (not whatever the client wrote
    into X-Forwarded-For), the raw header for forensics, and the Host the
    request was sent to.
    """
    from app.common.auth import verify_access_token
    from app.main import app
    from tests.conftest import seed_user

    token = seed_user(client, "log_attrib_user")
    user_id = verify_access_token(token).user_id

    # Served as the image serves it: uvicorn's defaults plus the proxy list
    # from the environment, reached through docker's bridge gateway.
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1,172.18.0.0/16")
    config = uvicorn.Config(app, log_config=None)
    config.load()
    behind_proxy = TestClient(
        cast(Any, config.loaded_app), client=("172.18.0.1", 40000)
    )
    behind_proxy.headers.update(client.headers)
    forwarded = "6.6.6.6, 203.0.113.9"

    with caplog.at_level(logging.INFO, logger="http"):
        r = behind_proxy.get(
            "http://www.cheese.example/notifications/unread-count",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Forwarded-For": forwarded,
                "User-Agent": "test-agent/1.0",
            },
        )
    assert r.status_code == 200

    req_lines = [
        rec.msg
        for rec in caplog.records
        if isinstance(rec.msg, dict) and rec.msg.get("event") == "req"
    ]
    line = next(
        ln for ln in req_lines if ln.get("path") == "/notifications/unread-count"
    )
    assert line.get("user") == user_id
    assert line.get("client") == "203.0.113.9"
    assert line.get("xff") == forwarded
    assert line.get("host") == "www.cheese.example"
    assert line.get("ua") == "test-agent/1.0"


def test_an_anonymous_request_line_has_no_user_field(client, caplog):
    """No auth → no attribution: the field is absent, not user=0/None."""
    with caplog.at_level(logging.INFO, logger="http"):
        client.get("/version")

    req_lines = [
        rec.msg
        for rec in caplog.records
        if isinstance(rec.msg, dict) and rec.msg.get("event") == "req"
    ]
    line = next(ln for ln in req_lines if ln.get("path") == "/version")
    assert "user" not in line


def test_a_healthcheck_knock_leaves_no_request_line(client, caplog):
    """`/readyz` is asked every 15 seconds by the container and `/healthz` by
    the rollout — 240 lines an hour that each say nothing happened. `/health`
    was already skipped for the same reason."""
    with caplog.at_level(logging.INFO, logger="http"):
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code in (200, 503)
        client.get("/version")

    paths = [
        rec.msg.get("path")
        for rec in caplog.records
        if isinstance(rec.msg, dict) and rec.msg.get("event") == "req"
    ]
    assert "/version" in paths
    assert "/readyz" not in paths
    assert "/healthz" not in paths


def test_a_refused_handshake_names_the_path(client, caplog):
    """A path that matches no route must say so, with the path attached."""
    with caplog.at_level(logging.WARNING, logger="app.ws"):
        # A refused handshake surfaces to the test client as a disconnect —
        # which is precisely what the browser sees, and why the UI could only
        # report a dropped connection.
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/topics/nope/chat"):
                pass

    assert any(
        "no route matched" in r.getMessage() and "/topics/nope/chat" in r.getMessage()
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]


def test_routine_calls_leave_no_line_and_failed_ones_still_do(capsys):
    """The journal keeps weeks only if the lines nobody reads are not written.

    Two sources were most of it: the device connection's access line for every
    internal call that went through, and the HTTP client's line for every
    request the backend made to it. A call that failed is what someone comes
    looking for, so that one still has to be there.
    """
    configure_logging()
    access = logging.getLogger("uvicorn.access")
    line = '%s - "%s %s HTTP/%s" %d'
    access.info(
        line, "1.2.3.4:5", "POST", "/internal/device-connection/call/exec", "1.1", 200
    )
    access.info(line, "1.2.3.4:5", "GET", "/healthz", "1.1", 200)
    access.info(
        line, "1.2.3.4:5", "POST", "/internal/device-connection/call/ping", "1.1", 502
    )
    access.info(line, "1.2.3.4:5", "POST", "/topics/t/execution/session-t", "1.1", 200)
    client = logging.getLogger("httpx")
    client.info("HTTP Request: POST http://device-connection:8082/internal/x 200 OK")
    client.warning("HTTP Request: POST http://device-connection:8082/internal/y failed")
    for handler in logging.getLogger().handlers:
        handler.flush()

    written = capsys.readouterr().err
    assert "/internal/device-connection/call/exec" not in written
    assert "/healthz" not in written
    assert "/internal/device-connection/call/ping" in written
    assert "/topics/t/execution/session-t" in written
    assert "/internal/x" not in written
    assert "/internal/y failed" in written


def test_the_tunnel_process_does_not_log_the_token_a_machine_dials_with(capsys):
    """The tunnel runs as its own process, so the backend's logging setup is
    not its own unless it installs it. uvicorn logs every accepted WebSocket
    with its whole path, and a machine dials `/llm/tunnel?token=…`."""
    import importlib

    import app.llm_tunnel_app as tunnel

    root = logging.getLogger()
    root.handlers.clear()
    importlib.reload(tunnel)
    logging.getLogger("uvicorn.error").info(
        '%s - "WebSocket %s" [accepted]',
        "1.2.3.4:5",
        "/llm/tunnel?token=LIVE-MACHINE-TOKEN",
    )
    for handler in root.handlers:
        handler.flush()

    written = capsys.readouterr().err
    assert "/llm/tunnel" in written
    assert "LIVE-MACHINE-TOKEN" not in written
