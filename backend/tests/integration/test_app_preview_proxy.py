"""Browser traffic reaches app previews through the real machine tunnel codec."""

import uuid

import pytest
from starlette.websockets import WebSocketDisconnect

from app.api.preview_host import cookie_name, preview_origin
from app.core.config import settings
from app.domain.agent import preview_tunnel as wire
from app.domain.agent.preview_hub import preview_hub
from tests.integration.conftest import session_auth_headers


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
        meta, body = wire.decode_meta(payload)
        self.asked.append(f"{meta['method']} {meta['path']}")
        self.requests.append((meta, body))
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


class EchoingMachine(FakeMachine):
    """Accept HMR connections and echo text and binary frames."""

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


@pytest.fixture
def preview_config(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "workspace"))
    monkeypatch.setattr(settings, "sites_domain", "sites.localhost")
    monkeypatch.setattr(settings, "sites_scheme", "http")
    monkeypatch.setattr(settings, "sites_port", None)
    monkeypatch.setattr(settings, "frontend_url", "http://platform.localhost")


def _project_topic(client, handle: str = "alice"):
    auth = session_auth_headers(handle)
    response = client.post(
        "/projects", json={"name": "Preview", "owner_handle": handle}, headers=auth
    )
    assert response.status_code == 200, response.text
    project = response.json()["data"]
    response = client.post(
        "/topics", json={"project_id": project["id"], "title": "Preview"}, headers=auth
    )
    assert response.status_code == 200, response.text
    return project, response.json()["data"]


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
    machine = EchoingMachine().attach(topic_id)
    try:
        response = client.post(
            f"/topics/{topic_id}/artifact",
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


def test_offline_app_returns_404(client, app_preview):
    _, topic_id, machine = app_preview
    _open_preview(client, topic_id)
    machine.detach()
    assert client.get(preview_origin(topic_id) + "/").status_code == 404


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
    added = client.post(
        f"/projects/{project['id']}/members",
        json={"user_handle": "bob", "role": "member"},
        headers=owner,
    )
    assert added.status_code == 200, added.text
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
    page asking for an asset through it is asking for a preview that is not
    there, which is what an offline app already answers (404), not a fault of
    this server."""
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
    preview_hub.attach(topic_id, dead)
    try:
        response = client.get(preview_origin(topic_id) + "/src/main.ts")
    finally:
        preview_hub.detach(topic_id, dead)
    assert response.status_code == 404, response.text
