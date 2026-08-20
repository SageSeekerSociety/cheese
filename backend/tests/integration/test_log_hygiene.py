"""What the logs must and must not say.

Both of these come from one incident: a chat WebSocket whose path missed the
route by one `/api`. The request log printed the full session token, and the
refusal was an INFO line with no path — so the report that came back was 网络不
稳定 rather than a routing bug, and a live credential sat in `docker logs`.
"""

import logging
import sys

import pytest
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
        args=("1.2.3.4:5", "/topics/x/chat?token=LIVE-SESSION-TOKEN"),
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
    url = "/topics/abc/chat?token=eyJhbGciOiJIUzI1NiJ9.body.signature"

    scrubbed = scrub_secrets(url)

    assert "eyJhbGciOiJIUzI1NiJ9" not in scrubbed
    assert scrubbed == "/topics/abc/chat?token=***"


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
    ],
)
def test_ordinary_diagnostics_survive_the_filter(raw):
    """Over-scrubbing costs the thing these reports exist to provide."""
    assert scrub_secrets(raw) == raw


def test_the_filter_covers_records_from_other_libraries():
    """The leak came from uvicorn's logger — code we never call — so the filter
    has to sit on the handler, not on our own log statements."""
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "WebSocket %s" 403',
        args=("1.2.3.4:5", "/topics/x/chat?token=SECRETVALUE"),
        exc_info=None,
    )

    RedactSecrets().filter(record)

    assert "SECRETVALUE" not in record.getMessage()


def test_the_request_line_names_the_user_and_forwarded_client(client, caplog):
    """The "req" line must say WHO, not just what.

    From the 2026-08-10 account-link incident (#222): every request logged the
    edge proxy's address as its peer, and the request line carried no user id —
    so telling two people's browsers apart took correlating adjacent requests
    by hand. The line must carry the authenticated user id and the verbatim
    X-Forwarded-For / User-Agent whenever they are present.
    """
    from app.common.auth import decode_token
    from tests.conftest import seed_user

    token = seed_user(client, "log_attrib_user")
    user_id = int(decode_token(token)["sub"])

    with caplog.at_level(logging.INFO, logger="http"):
        r = client.get(
            "/notifications/unread-count",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Forwarded-For": "10.9.8.7",
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
    assert line.get("client") == "10.9.8.7"
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
