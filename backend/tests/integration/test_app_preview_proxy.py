"""运行环境预览 reverse proxy — the route that makes a container's app reachable.

Without it `/preview` handed the browser `http://127.0.0.1:<host-port>`, which is
the *server's* loopback: only someone running the whole platform locally ever saw
anything. These pin the replacement — same authorization posture as the 现场
terminal, and no wider.
"""

import uuid

from fastapi.responses import Response

from app.api import proxy
from app.api.routes import app_preview
from app.domain.workspace import service as ws
from tests.integration.test_connector_viewer import _login


def _serving(served: list[str], body: bytes = b"<html><body>hi</body></html>"):
    async def _forward(endpoint, path, _request):
        served.append(f"http://{endpoint}/{path}")
        return Response(content=body, status_code=200, media_type="text/html")

    return _forward


def _project_topic(client, handle: str = "alice"):
    project = client.post(
        "/projects", json={"name": "P", "owner_handle": handle}
    ).json()["data"]
    topic = client.post(
        "/topics", json={"project_id": project["id"], "title": "t"}
    ).json()["data"]
    return project, topic


def test_the_app_proxy_requires_a_credential(client, monkeypatch):
    """The app is whatever the agent started in the project's workspace — a topic
    UUID must not be enough to read it."""
    served: list[str] = []
    monkeypatch.setattr(ws, "app_endpoint", lambda _t: "127.0.0.1:55007")
    monkeypatch.setattr(proxy, "forward", _serving(served))

    resp = client.get(f"/topics/{uuid.uuid4()}/app/")

    assert resp.status_code == 404, resp.text
    assert not served, "the request reached the app without any credential"


def test_a_member_reaches_the_app(client, monkeypatch):
    served: list[str] = []
    monkeypatch.setattr(ws, "app_endpoint", lambda _t: "127.0.0.1:55007")
    monkeypatch.setattr(proxy, "forward", _serving(served))

    _project, topic = _project_topic(client)
    token = _login(client, "alice")

    resp = client.get(f"/topics/{topic['id']}/app/index.html?token={token}")

    assert resp.status_code == 200, resp.text
    assert served == ["http://127.0.0.1:55007/index.html"], served


def test_root_absolute_asset_urls_are_moved_onto_the_proxy_prefix(client, monkeypatch):
    """The app is served under a sub-path, so a page asking for `/assets/x.js`
    would miss the container entirely and hit the platform SPA instead."""
    monkeypatch.setattr(ws, "app_endpoint", lambda _t: "127.0.0.1:55007")
    monkeypatch.setattr(
        proxy,
        "forward",
        _serving([], b'<html><script src="/main.js"></script><a href="//x/y"></a>'),
    )

    _project, topic = _project_topic(client)
    token = _login(client, "alice")

    body = client.get(f"/topics/{topic['id']}/app/?token={token}").text

    assert f'src="/api/topics/{topic["id"]}/app/main.js"' in body, body  # 浏览器侧
    assert 'href="//x/y"' in body, "protocol-relative URLs must be left alone"


def test_the_app_page_leaves_a_cookie_scoped_to_its_own_path(client, monkeypatch):
    """Assets and HMR are fetched by the page itself and carry no query string."""
    monkeypatch.setattr(ws, "app_endpoint", lambda _t: "127.0.0.1:55007")
    monkeypatch.setattr(proxy, "forward", _serving([]))

    _project, topic = _project_topic(client)
    token = _login(client, "alice")

    resp = client.get(f"/topics/{topic['id']}/app/?token={token}")

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


def test_no_app_running_is_a_404_not_a_crash(client, monkeypatch):
    monkeypatch.setattr(ws, "app_endpoint", lambda _t: None)

    _project, topic = _project_topic(client)
    token = _login(client, "alice")

    resp = client.get(f"/topics/{topic['id']}/app/?token={token}")

    assert resp.status_code == 404, resp.text
