"""What the logs must and must not say.

Both of these come from one incident: a chat WebSocket whose path missed the
route by one `/api`. The request log printed the full session token, and the
refusal was an INFO line with no path — so the report that came back was 网络不
稳定 rather than a routing bug, and a live credential sat in `docker logs`.
"""

import logging

import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.logging import RedactSecrets, _scrub


def test_a_session_token_never_reaches_the_log():
    """Browsers cannot set a header on a WebSocket, so every WS carries the token
    in its query string — and the access log prints whole URLs."""
    url = "/topics/abc/chat?token=eyJhbGciOiJIUzI1NiJ9.body.signature"

    scrubbed = _scrub(url)

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
    assert "***" in _scrub(raw)


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
