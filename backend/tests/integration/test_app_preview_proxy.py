"""Browser traffic reaches app previews through the real machine tunnel codec."""

import time
import uuid

import pytest
from starlette.websockets import WebSocketDisconnect

from app.api.preview_host import cookie_name, preview_origin
from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import preview_tunnel as wire
from app.domain.agent.preview_hub import PreviewMachine, preview_hub
from tests.integration.conftest import (
    add_external_member,
    post_project,
    session_auth_headers,
)


class FakeMachine:
    """Answer requests using the frames sent by the machine helper."""

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
        self.requests: list[tuple[dict, bytes]] = []
        self.machine: PreviewMachine | None = None

    def attach(self, topic_id: uuid.UUID, seat: str) -> "FakeMachine":
        self.machine = preview_hub.attach(topic_id, seat, self)
        return self

    def detach(self) -> None:
        if self.machine is not None:
            preview_hub.detach(self.machine)

    def reply(self, data: bytes) -> None:
        assert self.machine is not None
        self.machine.on_frame(data)

    async def send_bytes(self, data: bytes) -> None:
        op, stream, payload = wire.decode(data)
        if op != wire.OP_REQ:
            return
        meta, body = wire.decode_meta(payload)
        self.asked.append(f"{meta['method']} {meta['path']}")
        self.requests.append((meta, body))
        self.reply(
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
        self.reply(wire.encode(wire.OP_END, stream))


class EchoingMachine(FakeMachine):
    """Accept HMR connections and echo text and binary frames."""

    def __init__(self) -> None:
        super().__init__()
        self.ws_path = ""
        self.ws_headers: list[list[str]] = []

    async def send_bytes(self, data: bytes) -> None:
        op, stream, payload = wire.decode(data)
        if op == wire.OP_WS_OPEN:
            meta, _ = wire.decode_meta(payload)
            self.ws_path = meta["path"]
            self.ws_headers = meta["headers"]
            self.reply(
                wire.encode(
                    wire.OP_WS_OK, stream, wire.encode_meta({"subprotocol": "vite-hmr"})
                ),
            )
            return
        if op == wire.OP_WS_MSG:
            self.reply(wire.encode(wire.OP_WS_MSG, stream, payload))
            return
        await super().send_bytes(data)


@pytest.fixture
def preview_config(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "workspace"))
    monkeypatch.setattr(settings, "sites_domain", "sites.localhost")
    monkeypatch.setattr(settings, "sites_scheme", "http")
    monkeypatch.setattr(settings, "sites_port", None)
    monkeypatch.setattr(settings, "frontend_url", "http://platform.localhost")


def _project_topic(client, handle: str = "alice"):
    auth = session_auth_headers(handle)
    response = post_project(
        client, json={"name": "Preview"}, headers=auth, owner=handle
    )
    assert response.status_code == 200, response.text
    project = response.json()["data"]
    response = client.post(
        "/topics", json={"project_id": project["id"], "title": "Preview"}, headers=auth
    )
    assert response.status_code == 200, response.text
    return project, response.json()["data"]


def _room_agent(client, topic_id) -> str:
    """The teammate a room answers as — who a declaration made by a person is
    recorded as, and so whose tunnel the room's preview follows."""
    response = client.get(
        f"/topics/{topic_id}/members", headers=session_auth_headers("alice")
    )
    assert response.status_code == 200, response.text
    rows = response.json()["data"]["data"]
    return next(row["member_handle"] for row in rows if row["agent"])


def _open_preview(client, topic_id, handle="alice", path="/"):
    response = client.post(
        f"/topics/{topic_id}/preview-session", headers=session_auth_headers(handle)
    )
    assert response.status_code == 200, response.text
    grant = response.json()["data"]
    exchange = client.post(
        grant["url"],
        data={"grant": grant["grant"], "path": path},
        headers={"Origin": settings.frontend_url},
        follow_redirects=False,
    )
    assert exchange.status_code == 303, exchange.text
    return grant, exchange


@pytest.fixture
def app_preview(client, preview_config):
    project, topic = _project_topic(client)
    topic_id = uuid.UUID(topic["id"])
    machine = EchoingMachine().attach(topic_id, _room_agent(client, topic_id))
    try:
        response = client.post(
            f"/topics/{topic_id}/shown",
            json={"path": "http://localhost:5173", "as": "app"},
            headers=session_auth_headers("alice"),
        )
        assert response.status_code == 200, response.text
        machine.asked.clear()
        machine.requests.clear()
        yield project, topic_id, machine
    finally:
        machine.detach()


def test_app_requires_preview_cookie_before_contacting_machine(client, app_preview):
    _, topic_id, machine = app_preview
    origin = preview_origin(topic_id)
    for headers in ({}, session_auth_headers("alice")):
        response = client.get(origin + "/", headers=headers)
        assert response.status_code == 401, response.text
    assert not machine.asked


@pytest.mark.parametrize(
    ("path", "mime", "body"),
    [
        ("/", "text/html", b'<script type="module" src="/main.js"></script>'),
        ("/main.js", "text/javascript", b'import "/dependency.js";'),
        ("/style.css", "text/css", b'body { background: url("/image.svg") }'),
        ("/image.svg", "image/svg+xml", b'<svg xmlns="http://www.w3.org/2000/svg"/>'),
    ],
)
def test_app_assets_keep_root_urls_bytes_and_query(
    client, app_preview, path, mime, body
):
    _, topic_id, machine = app_preview
    _open_preview(client, topic_id)
    machine.body, machine.content_type = body, mime
    response = client.get(preview_origin(topic_id) + path + "?x=1&token=app-value&x=2")
    assert response.status_code == 200, response.text
    assert response.content == body
    assert response.headers["content-type"].startswith(mime)
    assert machine.asked == [f"GET {path}?x=1&token=app-value&x=2"]
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_app_methods_body_and_api_paths_reach_only_the_app(client, app_preview, method):
    _, topic_id, machine = app_preview
    _open_preview(client, topic_id)
    preview_cookie = client.cookies.get(cookie_name())
    machine.body = b"upstream app response"
    machine.status = 202
    machine.extra_headers = [["x-app-response", "yes"]]
    response = client.request(
        method,
        preview_origin(topic_id) + "/api/projects?token=app-value&v=one%2Ftwo",
        content=b"\x00\xffpayload",
        headers={
            "Authorization": "Bearer app-access-token",
            "Cookie": f"{cookie_name()}={preview_cookie}; app-session=app-cookie",
            "X-Cheese-Token": "platform-secret",
            "X-App-Request": "yes",
            "Content-Type": "application/octet-stream",
        },
    )
    assert response.status_code == 202, response.text
    assert response.content == b"upstream app response"
    assert response.headers["x-app-response"] == "yes"
    assert "set-cookie" not in response.headers
    meta, body = machine.requests[-1]
    assert meta["method"] == method
    assert meta["path"] == "/api/projects?token=app-value&v=one%2Ftwo"
    assert body == b"\x00\xffpayload"
    headers = {key.lower(): value for key, value in meta["headers"]}
    assert headers["x-app-request"] == "yes"
    assert headers["authorization"] == "Bearer app-access-token"
    assert headers["cookie"] == "app-session=app-cookie"
    assert not any(key.startswith("x-cheese-") for key in headers)
    assert preview_cookie not in str(meta)


@pytest.mark.parametrize(
    "same_site",
    ["", "; SameSite=Lax", "; SameSite=Strict", "; SameSite=None; Partitioned"],
)
def test_app_login_cookies_are_host_only_and_cannot_replace_preview_access(
    client, app_preview, monkeypatch, same_site
):
    monkeypatch.setattr(settings, "sites_scheme", "https")
    _, topic_id, machine = app_preview
    _open_preview(client, topic_id)
    preview_cookie = client.cookies.get(cookie_name())
    machine.extra_headers = [
        [
            "set-cookie",
            "app-session=logged-in; Domain=sites.localhost; Path=/;"
            " HttpOnly; Max-Age=3600" + same_site,
        ],
        ["set-cookie", "cheese-preview-local=forged; Path=/"],
        ["set-cookie", "__Host-cheese-preview=forged; Secure; Path=/"],
    ]
    origin = preview_origin(topic_id)
    response = client.post(origin + "/login", content=b"app login")
    assert response.status_code == 200, response.text
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 1, cookies
    assert cookies[0].startswith("app-session=logged-in;")
    assert "domain=" not in cookies[0].lower()
    assert "httponly" in cookies[0].lower()
    assert "Secure" in cookies[0]
    assert "SameSite=None" in cookies[0]
    assert cookies[0].count("Partitioned") == 1
    assert "Max-Age=3600" in cookies[0]
    assert "Path=/" in cookies[0]
    assert client.cookies.get(cookie_name()) == preview_cookie
    assert client.get(origin + "/account").status_code == 200
    meta, _ = machine.requests[-1]
    headers = {key.lower(): value for key, value in meta["headers"]}
    assert headers["cookie"] == "app-session=logged-in"


@pytest.mark.parametrize("sender", ["other-preview", "platform", "external"])
def test_cross_origin_posts_cannot_use_an_existing_preview_session(
    client, app_preview, sender
):
    _, topic_id, machine = app_preview
    _open_preview(client, topic_id)
    origin = preview_origin(topic_id)
    sender_origin = {
        "other-preview": preview_origin(uuid.uuid4()),
        "platform": settings.frontend_url,
        "external": "https://attacker.example",
    }[sender]
    response = client.post(
        origin + "/delete-account",
        headers={"Origin": sender_origin},
        data={"confirm": "yes"},
    )
    assert response.status_code == 403, response.text
    assert not machine.asked
    response = client.post(
        origin + "/save", headers={"Origin": origin}, content=b"same-origin edit"
    )
    assert response.status_code == 200, response.text
    assert machine.asked == ["POST /save"]
    assert machine.requests[-1][1] == b"same-origin edit"


@pytest.mark.parametrize("site", ["same-site", "cross-site"])
def test_cross_origin_subresources_without_origin_do_not_reach_app(
    client, app_preview, site
):
    _, topic_id, machine = app_preview
    _open_preview(client, topic_id)
    origin = preview_origin(topic_id)
    response = client.get(
        origin + "/logout",
        headers={"Sec-Fetch-Site": site, "Sec-Fetch-Mode": "no-cors"},
    )
    assert response.status_code == 403
    assert not machine.asked
    response = client.get(
        origin + "/asset.js",
        headers={"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "no-cors"},
    )
    assert response.status_code == 200
    assert machine.asked == ["GET /asset.js"]


def test_offline_app_answers_transiently_not_gone(client, app_preview):
    """An app behind a detached tunnel is briefly unreachable, not gone. A bare
    404 here renders a navigation as a white frame and tells every client the
    preview does not exist; a 503 with Retry-After says to come back."""
    _, topic_id, machine = app_preview
    _open_preview(client, topic_id)
    machine.detach()
    response = client.get(preview_origin(topic_id) + "/")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "2"
    assert response.headers["X-Cheese-Preview-State"] == "app_unavailable"


def test_hmr_preserves_query_subprotocol_and_both_frame_types(client, app_preview):
    _, topic_id, machine = app_preview
    _open_preview(client, topic_id)
    origin = preview_origin(topic_id)
    preview_cookie = client.cookies.get(cookie_name())
    with client.websocket_connect(
        origin.replace("http://", "ws://") + "/@vite/client?token=app-value&x=1&x=2",
        headers={
            "Origin": origin,
            "Authorization": "Bearer app-access-token",
            "Cookie": f"{cookie_name()}={preview_cookie}; app-session=app-cookie",
            "X-Cheese-Token": "platform-secret",
        },
        subprotocols=["vite-hmr"],
    ) as socket:
        assert socket.accepted_subprotocol == "vite-hmr"
        socket.send_text("ping")
        assert socket.receive_text() == "ping"
        socket.send_bytes(b"\x00\x01")
        assert socket.receive_bytes() == b"\x00\x01"
    assert machine.ws_path == "/@vite/client?token=app-value&x=1&x=2"
    forwarded = {key.lower(): value for key, value in machine.ws_headers}
    assert forwarded["sec-websocket-protocol"] == "vite-hmr"
    assert forwarded["authorization"] == "Bearer app-access-token"
    assert forwarded["cookie"] == "app-session=app-cookie"
    assert not forwarded.keys() & {
        "x-cheese-token",
        "sec-websocket-key",
        "sec-websocket-version",
        "sec-websocket-extensions",
    }


@pytest.mark.parametrize(
    "has_cookie,origin_header", [(False, "preview"), (True, "platform")]
)
def test_hmr_refuses_missing_cookie_or_cross_origin(
    client, app_preview, has_cookie, origin_header
):
    _, topic_id, machine = app_preview
    if has_cookie:
        _open_preview(client, topic_id)
    origin = preview_origin(topic_id)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            origin.replace("http://", "ws://") + "/ws",
            headers={
                "Origin": origin
                if origin_header == "preview"
                else settings.frontend_url
            },
        ) as socket:
            socket.receive_text()
    assert machine.ws_path == ""


def test_membership_revocation_blocks_http_and_new_hmr_connection(client, app_preview):
    project, topic_id, machine = app_preview
    owner = session_auth_headers("alice")
    add_external_member(client, project["id"], "bob", by="alice")
    _open_preview(client, topic_id, "bob")
    origin = preview_origin(topic_id)
    assert client.get(origin + "/").status_code == 200
    removed = client.delete(f"/projects/{project['id']}/members/bob", headers=owner)
    assert removed.status_code == 200, removed.text
    machine.asked.clear()
    assert client.get(origin + "/main.js").status_code == 404
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            origin.replace("http://", "ws://") + "/ws", headers={"Origin": origin}
        ) as socket:
            socket.receive_text()
    assert not machine.asked and machine.ws_path == ""


def test_the_tunnel_refuses_a_caller_that_cannot_name_a_topic(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/preview/tunnel?token=not-a-token") as socket:
            socket.receive_bytes()


def test_a_tunnel_whose_peer_dropped_is_an_absent_preview_not_a_fault(
    client, app_preview
):
    """The helper's socket dies under a write — the machine went to sleep, the
    laptop closed — and the hub only hears of it from the write that fails. A
    page asking for an asset through it is asking for a preview that is briefly
    unreachable: a transient 503, so an asset client backs off instead of
    reading the preview as gone. It is not a fault of this server."""
    import asyncio

    from starlette.websockets import WebSocket

    from app.api.routes.app_preview import _WebSocketPreviewTransport

    _, topic_id, machine = app_preview
    _open_preview(client, topic_id)
    machine.detach()

    async def peer_gone() -> WebSocket:
        # A real Starlette socket, accepted, whose next write finds the peer gone:
        # that is the exact path uvicorn takes to a 1006 disconnect.
        async def receive() -> dict:
            return {"type": "websocket.connect"}

        async def send(message: dict) -> None:
            if message["type"] != "websocket.accept":
                raise OSError("Broken pipe")

        websocket = WebSocket({"type": "websocket"}, receive, send)
        await websocket.accept()
        return websocket

    dead = _WebSocketPreviewTransport(asyncio.run(peer_gone()))
    attached = preview_hub.attach(topic_id, _room_agent(client, topic_id), dead)
    assert attached is not None
    try:
        response = client.get(preview_origin(topic_id) + "/src/main.ts")
    finally:
        preview_hub.detach(attached)
    assert response.status_code == 503, response.text


# --- one tunnel per teammate ----------------------------------------------------


def _tunnel_url(project, topic_id, seat: str) -> str:
    token = mint_scoped_token(
        project_id=project["id"], topic_id=str(topic_id), agent_handle=seat
    )
    return f"/preview/tunnel?token={token}"


def _eventually(check) -> None:
    """The route attaches right after it accepts, so the client can see the
    upgrade a moment before the hub sees the helper."""
    deadline = time.monotonic() + 5
    while not check():
        assert time.monotonic() < deadline, "the tunnel never attached"
        time.sleep(0.01)


def test_two_teammates_in_one_room_each_keep_their_tunnel(client, preview_config):
    """Two teammates in one room serve from their own checkouts and each start a
    helper. Keyed by the room alone, each arrival hung up on the other, the one
    hung up dialled back a second later and did the same, and a page load that
    fell between the two saw `preview unavailable`."""
    project, topic = _project_topic(client)
    topic_id = uuid.UUID(topic["id"])

    with client.websocket_connect(_tunnel_url(project, topic_id, "cheese-one")):
        _eventually(lambda: preview_hub.is_online(topic_id, "cheese-one"))
        with client.websocket_connect(_tunnel_url(project, topic_id, "cheese-two")):
            _eventually(lambda: preview_hub.is_online(topic_id, "cheese-two"))
            assert preview_hub.is_online(topic_id, "cheese-one")


def test_a_relaunched_teammate_tells_its_old_helper_to_stop(client, preview_config):
    """The same teammate relaunched, or moved to another machine: the new helper
    takes the tunnel, and the old one is told so in a way it does not answer by
    dialling straight back in."""
    project, topic = _project_topic(client)
    topic_id = uuid.UUID(topic["id"])
    url = _tunnel_url(project, topic_id, "cheese-one")

    with client.websocket_connect(url) as old:
        _eventually(lambda: preview_hub.is_online(topic_id, "cheese-one"))
        with client.websocket_connect(url):
            message = old.receive()
            assert message["type"] == "websocket.close"
            assert message["code"] == wire.CLOSE_SUPERSEDED
            assert message["reason"], "the old helper's log has to say why"


def test_a_stale_helper_cannot_take_the_tunnel_back(client, preview_config):
    """A helper left on the machine a teammate moved off — a laptop waking up
    hours later — still holds a valid credential. It is older than the one the
    live helper holds, so it is refused rather than allowed to displace it."""
    project, topic = _project_topic(client)
    topic_id = uuid.UUID(topic["id"])
    stale = _tunnel_url(project, topic_id, "cheese-one")
    time.sleep(1.05)  # credentials are dated to the second
    current = _tunnel_url(project, topic_id, "cheese-one")

    with client.websocket_connect(current):
        _eventually(lambda: preview_hub.is_online(topic_id, "cheese-one"))
        with client.websocket_connect(stale) as late:
            message = late.receive()
            assert message["type"] == "websocket.close"
            assert message["code"] == wire.CLOSE_SUPERSEDED
        assert preview_hub.is_online(topic_id, "cheese-one")


def test_the_room_preview_shows_the_app_of_the_teammate_who_served_it(
    client, preview_config
):
    """Each teammate's app runs in its own checkout behind its own tunnel, so
    the room's preview has to reach the one the declaring teammate is serving,
    not whichever helper connected last."""
    project, topic = _project_topic(client)
    topic_id = uuid.UUID(topic["id"])
    made = client.post(
        f"/projects/{project['id']}/agents",
        json={"handle": "planner", "display_name": "规划师"},
        headers=session_auth_headers("alice"),
    )
    assert made.status_code == 200, made.text
    teammate = made.json()["data"]["seat_handle"]
    joined = client.post(
        f"/topics/{topic_id}/members",
        json={"handle": teammate, "role": "member"},
        headers=session_auth_headers("alice"),
    )
    assert joined.status_code == 200, joined.text
    theirs = FakeMachine(body=b"the teammate's app").attach(topic_id, teammate)
    other = FakeMachine(body=b"another app").attach(
        topic_id, _room_agent(client, topic_id)
    )
    try:
        declared = client.post(
            f"/topics/{topic_id}/shown",
            json={"path": "dev server", "as": "app"},
            headers={
                "X-Cheese-Token": mint_scoped_token(
                    project_id=project["id"],
                    topic_id=str(topic_id),
                    agent_handle=teammate,
                )
            },
        )
        assert declared.status_code == 200, declared.text
        _open_preview(client, topic_id)

        response = client.get(preview_origin(topic_id) + "/")

        assert response.status_code == 200, response.text
        assert response.content == b"the teammate's app"
        assert other.asked == []
    finally:
        theirs.detach()
        other.detach()
