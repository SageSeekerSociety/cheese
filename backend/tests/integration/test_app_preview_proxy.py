"""运行环境预览 over the machine's own tunnel — the route that makes an app the
agent started on someone else's laptop reachable from a browser.

Every machine a turn runs on is behind NAT with zero inbound ports, so there is
no address the platform can dial. What these pin is the replacement transport:
the machine dials out, the browser's requests ride that connection, and the
authorization posture stays exactly the 现场 terminal's — no wider.

They talk to the REAL hub over the REAL frame codec; only the machine at the far
end is a stand-in, and it answers the way the shipped helper does.
"""

import uuid

import pytest
from starlette.websockets import WebSocketDisconnect

from app.api.routes import app_preview
from app.domain.agent import preview_tunnel as wire
from app.domain.agent.preview_hub import preview_hub
from tests.integration.test_connector_viewer import _login


class FakeMachine:
    """A machine whose helper answers every request with one canned response.

    Speaks the same frames the shipped helper speaks — the test's whole point is
    that the route and the helper agree on the wire, so nothing here is allowed
    to short-circuit it.
    """

    def __init__(
        self,
        body: bytes = b"<html><body>hi</body></html>",
        status: int = 200,
        content_type: str = "text/html",
        extra_headers: list[list[str]] | None = None,
    ) -> None:
        self.body = body
        self.status = status
        self.content_type = content_type
        self.extra_headers = extra_headers or []
        self.asked: list[str] = []
        self.topic_id: uuid.UUID | None = None

    def attach(self, topic_id: uuid.UUID) -> "FakeMachine":
        self.topic_id = topic_id
        preview_hub.attach(topic_id, self)
        return self

    def detach(self) -> None:
        if self.topic_id is not None:
            preview_hub.detach(self.topic_id, self)

    async def send_bytes(self, data: bytes) -> None:
        assert self.topic_id is not None
        op, stream, payload = wire.decode(data)
        if op != wire.OP_REQ:
            return
        meta, _ = wire.decode_meta(payload)
        self.asked.append(f"{meta['method']} {meta['path']}")
        preview_hub.on_frame(
            self.topic_id,
            wire.encode(
                wire.OP_RESP,
                stream,
                wire.encode_meta(
                    {
                        "status": self.status,
                        "headers": [
                            ["content-type", self.content_type],
                            *self.extra_headers,
                        ],
                    },
                    self.body,
                ),
            ),
        )
        preview_hub.on_frame(self.topic_id, wire.encode(wire.OP_END, stream))


def _project_topic(client, handle: str = "alice"):
    project = client.post(
        "/projects", json={"name": "P", "owner_handle": handle}
    ).json()["data"]
    topic = client.post(
        "/topics", json={"project_id": project["id"], "title": "t"}
    ).json()["data"]
    return project, topic


def test_the_app_proxy_requires_a_credential(client):
    """The app is whatever the agent started on that machine — a topic UUID must
    not be enough to read it."""
    topic_id = uuid.uuid4()
    machine = FakeMachine().attach(topic_id)
    try:
        resp = client.get(f"/topics/{topic_id}/app/")
    finally:
        machine.detach()

    assert resp.status_code == 404, resp.text
    assert not machine.asked, "the request reached the app without any credential"


def test_a_member_reaches_the_app(client):
    _project, topic = _project_topic(client)
    token = _login(client, "alice")
    machine = FakeMachine().attach(uuid.UUID(topic["id"]))
    try:
        resp = client.get(f"/topics/{topic['id']}/app/index.html?x=1&token={token}")
    finally:
        machine.detach()

    assert resp.status_code == 200, resp.text
    assert resp.content == b"<html><body>hi</body></html>", resp.content
    # The query string travels with the request — a dev server routes on it —
    # but the credential does NOT. The page is written by the agent, and a
    # session token reaching it (readable in `location.search`, logged by its own
    # server) is the one thing this surface exists to prevent.
    assert machine.asked == ["GET /index.html?x=1"], machine.asked
    assert token not in machine.asked[0]


def test_the_sandboxed_frame_is_allowed_to_read_what_it_fetched(client):
    """The frame is sandboxed WITHOUT ``allow-same-origin``, so the browser gives
    it an opaque origin and every sub-request it makes carries ``Origin: null``.

    A ``<script type="module">`` — how essentially every current frontend loads
    itself — is always fetched in CORS mode, unlike a classic script tag. Without
    this header the browser throws away a perfectly good 200 the moment it
    arrives and the app never executes a line, which reads as a blank preview.
    """
    _project, topic = _project_topic(client)
    token = _login(client, "alice")
    machine = FakeMachine(b"export const x = 1", content_type="text/javascript").attach(
        uuid.UUID(topic["id"])
    )
    try:
        resp = client.get(
            f"/topics/{topic['id']}/app/main.js?token={token}",
            headers={"Origin": "null"},
        )
    finally:
        machine.detach()

    assert resp.status_code == 200, resp.text
    assert resp.headers["access-control-allow-origin"] == "*", dict(resp.headers)


def test_the_apps_own_cors_header_does_not_survive_next_to_ours(client):
    """A dev server that sets its own ``Access-Control-Allow-Origin`` (vite does)
    must not leave two of them on the way out: a browser rejects a response
    carrying the header twice, so a passthrough would break exactly the apps that
    tried hardest to be reachable."""
    _project, topic = _project_topic(client)
    token = _login(client, "alice")
    machine = FakeMachine(
        b"export const x = 1",
        content_type="text/javascript",
        extra_headers=[["access-control-allow-origin", "http://localhost:5173"]],
    ).attach(uuid.UUID(topic["id"]))
    try:
        resp = client.get(
            f"/topics/{topic['id']}/app/main.js?token={token}",
            headers={"Origin": "null"},
        )
    finally:
        machine.detach()

    assert resp.headers.get_list("access-control-allow-origin") == ["*"], dict(
        resp.headers
    )


def test_root_absolute_asset_urls_are_moved_onto_the_proxy_prefix(client):
    """The app is served under a sub-path, so a page asking for `/assets/x.js`
    would miss the machine entirely and hit the platform SPA instead."""
    _project, topic = _project_topic(client)
    token = _login(client, "alice")
    machine = FakeMachine(
        b'<html><script src="/main.js"></script><a href="//x/y"></a>'
    ).attach(uuid.UUID(topic["id"]))
    try:
        body = client.get(f"/topics/{topic['id']}/app/?token={token}").text
    finally:
        machine.detach()

    assert f'src="/api/topics/{topic["id"]}/app/main.js"' in body, body  # 浏览器侧
    assert 'href="//x/y"' in body, "protocol-relative URLs must be left alone"


def test_the_app_page_leaves_a_cookie_scoped_to_its_own_path(client):
    """Assets and HMR are fetched by the page itself and carry no query string."""
    _project, topic = _project_topic(client)
    token = _login(client, "alice")
    machine = FakeMachine().attach(uuid.UUID(topic["id"]))
    try:
        resp = client.get(f"/topics/{topic['id']}/app/?token={token}")
    finally:
        machine.detach()

    cookie = resp.headers.get("set-cookie", "")
    assert app_preview.COOKIE_NAME in cookie, cookie
    assert f"Path=/api/topics/{topic['id']}/app" in cookie, cookie


def test_the_handshake_mints_the_cookie_so_no_token_rides_in_the_iframe_url(client):
    """The frame renders whatever 芝士 chose to serve. A ``?token=`` in its src is
    readable by that page's own JS (``location.search``) even sandboxed, so the
    credential is handed over as an HttpOnly cookie by a separate call that uses
    the normal Authorization header instead."""
    _project, topic = _project_topic(client)
    token = _login(client, "alice")

    resp = client.get(
        f"/topics/{topic['id']}/app-session",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, resp.text
    cookie = resp.headers.get("set-cookie", "")
    assert app_preview.COOKIE_NAME in cookie, cookie
    assert f"Path=/api/topics/{topic['id']}/app" in cookie, cookie
    assert "HttpOnly" in cookie, cookie


def test_the_handshake_refuses_a_stranger(client):
    resp = client.get(f"/topics/{uuid.uuid4()}/app-session")
    assert resp.status_code == 404, resp.text


def test_no_tunnel_is_a_404_not_a_crash(client):
    """The machine is offline, or never carried a preview out at all."""
    _project, topic = _project_topic(client)
    token = _login(client, "alice")

    resp = client.get(f"/topics/{topic['id']}/app/?token={token}")

    assert resp.status_code == 404, resp.text


class EchoingMachine(FakeMachine):
    """Also accepts a WebSocket and echoes what it is sent — a dev server's HMR
    socket reduced to the only thing the proxy has to get right."""

    def __init__(self) -> None:
        super().__init__()
        self.ws_path = ""
        self.ws_headers: list[list[str]] = []

    async def send_bytes(self, data: bytes) -> None:
        assert self.topic_id is not None
        op, stream, payload = wire.decode(data)
        if op == wire.OP_WS_OPEN:
            meta, _ = wire.decode_meta(payload)
            self.ws_path = meta["path"]
            self.ws_headers = meta["headers"]
            preview_hub.on_frame(
                self.topic_id,
                wire.encode(
                    wire.OP_WS_OK, stream, wire.encode_meta({"subprotocol": "vite-hmr"})
                ),
            )
            return
        if op == wire.OP_WS_MSG:
            preview_hub.on_frame(
                self.topic_id, wire.encode(wire.OP_WS_MSG, stream, payload)
            )
            return
        await super().send_bytes(data)


def test_the_hmr_socket_reaches_the_app_and_carries_both_directions(client):
    """A dev server pushes reloads over a WebSocket. Without this leg the page
    loads once and then reconnects forever, which reads as a broken preview."""
    _project, topic = _project_topic(client)
    token = _login(client, "alice")
    machine = EchoingMachine().attach(uuid.UUID(topic["id"]))
    try:
        with client.websocket_connect(
            f"/topics/{topic['id']}/app/@vite/client?token={token}",
            subprotocols=["vite-hmr"],
        ) as ws:
            ws.send_text("ping")
            assert ws.receive_text() == "ping"
            ws.send_bytes(b"\x00\x01")
            assert ws.receive_bytes() == b"\x00\x01"
    finally:
        machine.detach()

    assert machine.ws_path == "/@vite/client", machine.ws_path
    forwarded = {k.lower() for k, _ in machine.ws_headers}
    # This leg's own handshake stops here: the machine performs its own, and a
    # relayed key or a negotiated compression extension corrupts that one.
    assert not forwarded & {"sec-websocket-key", "sec-websocket-extensions"}
    # The subprotocol is the exception — it is negotiated end to end.
    assert "sec-websocket-protocol" in forwarded


def test_the_hmr_socket_refuses_a_caller_without_a_credential(client):
    _project, topic = _project_topic(client)
    machine = EchoingMachine().attach(uuid.UUID(topic["id"]))
    try:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/topics/{topic['id']}/app/ws") as ws:
                ws.receive_text()
    finally:
        machine.detach()
    assert machine.ws_path == "", "the handshake reached the app unauthenticated"


def test_the_tunnel_refuses_a_caller_that_cannot_name_a_topic(client):
    """Whoever dials in decides what the room sees, so the token IS the routing
    table: no valid topic claim, no tunnel."""
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/preview/tunnel?token=not-a-token") as ws:
            ws.receive_bytes()
