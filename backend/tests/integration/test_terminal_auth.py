"""Who can open a topic's live terminal, and when the frontend is told it exists.

The status endpoint is what decides whether 现场 embeds an iframe or shows the
施工记录 timeline, and the two routes that actually carry the pane — the ttyd HTTP
proxy and its WebSocket — take only a topic id. In a default test environment they
answer 404 because no container is up, which hides both questions; these tests pin
them with the endpoint present.
"""

import uuid

from fastapi.responses import Response

from app.api import proxy
from app.api.routes import terminal
from tests.integration.test_connector_viewer import _login


def _serving(served: list[str]):
    """Stand in for the upstream hop, recording that it was reached at all."""

    async def _forward(endpoint, path, _request):
        served.append(f"http://{endpoint}/{path}")
        return Response(content=b"<ttyd/>", status_code=200, media_type="text/html")

    return _forward


def _reachable(alive: bool):
    async def _probe(_endpoint, **_kw):
        return alive

    return _probe


def _project_topic(client, handle: str = "alice"):
    project = client.post(
        "/api/projects", json={"name": "T", "owner_handle": handle}
    ).json()["data"]
    topic = client.post(
        "/api/topics", json={"project_id": project["id"], "title": "t"}
    ).json()["data"]
    return project, topic


def test_the_ttyd_proxy_requires_a_credential(client, monkeypatch):
    """An unauthenticated request must not reach the pane.

    The pane shows whatever the agent is doing — file contents, command output,
    anything it echoes. A topic id is a UUID, but that is obscurity, not
    authorization.
    """
    served: list[str] = []
    monkeypatch.setattr(terminal, "_live_endpoint", lambda _t: "127.0.0.1:7681")
    monkeypatch.setattr(proxy, "forward", _serving(served))

    resp = client.get(f"/api/topics/{uuid.uuid4()}/terminal/live/")

    assert resp.status_code in (401, 403, 404), (
        f"unauthenticated caller got {resp.status_code} and the proxy "
        f"{'served ' + served[0] if served else 'was not reached'}"
    )
    assert not served, "the request reached ttyd without any credential"


def test_a_project_member_still_gets_the_pane(client, monkeypatch):
    """The guard must not lock out the people the feature is for."""
    served: list[str] = []
    monkeypatch.setattr(terminal, "_live_endpoint", lambda _t: "127.0.0.1:7681")
    monkeypatch.setattr(proxy, "forward", _serving(served))

    _project, topic = _project_topic(client)
    token = _login(client, "alice")

    resp = client.get(f"/api/topics/{topic['id']}/terminal/live/?token={token}")

    assert resp.status_code == 200, resp.text
    assert served, "the owner should have reached ttyd"


def test_the_pane_leaves_a_scoped_cookie_for_its_own_subrequests(client, monkeypatch):
    """ttyd's page fetches `/token` (and assets) by itself, and those requests
    carry no query string — so the page load re-issues the credential as a cookie
    scoped to this topic's terminal path, and nothing wider."""
    monkeypatch.setattr(terminal, "_live_endpoint", lambda _t: "127.0.0.1:7681")
    monkeypatch.setattr(proxy, "forward", _serving([]))

    _project, topic = _project_topic(client)
    token = _login(client, "alice")

    resp = client.get(f"/api/topics/{topic['id']}/terminal/live/?token={token}")

    cookie = resp.headers.get("set-cookie", "")
    assert terminal.COOKIE_NAME in cookie, cookie
    assert f"Path=/api/topics/{topic['id']}/terminal" in cookie, cookie


def test_a_subrequest_authenticates_with_that_cookie_alone(client, monkeypatch):
    """The follow-up fetch (no ?token=) must still be served — otherwise the pane
    loads and then fails on its very first sub-request."""
    served: list[str] = []
    monkeypatch.setattr(terminal, "_live_endpoint", lambda _t: "127.0.0.1:7681")
    monkeypatch.setattr(proxy, "forward", _serving(served))

    _project, topic = _project_topic(client)
    token = _login(client, "alice")
    client.get(f"/api/topics/{topic['id']}/terminal/live/?token={token}")

    resp = client.get(f"/api/topics/{topic['id']}/terminal/live/token")

    assert resp.status_code == 200, resp.text
    assert served, "the cookie-bearing sub-request should have reached ttyd"


def test_status_says_unavailable_without_a_credential(client, monkeypatch):
    """现场 must fall back to the 施工记录 timeline, not embed a frame that 404s.

    This is the whole reason the terminal shipped as a white box: `available` was
    computed from the port mapping alone, so the panel replaced the timeline with
    an iframe the proxy would refuse — leaving no visible content and no way back.
    """
    monkeypatch.setattr(terminal.settings, "agent_backend", "tmux")
    monkeypatch.setattr(terminal, "_live_endpoint", lambda _t: "127.0.0.1:7681")
    monkeypatch.setattr(proxy, "probe", _reachable(True))

    _project, topic = _project_topic(client)

    data = client.get(f"/api/topics/{topic['id']}/terminal").json()["data"]

    assert data["available"] is False, data
    assert "url" not in data


def test_status_says_unavailable_when_nothing_answers_on_the_port(client, monkeypatch):
    """A published port is not a running ttyd: a container whose pane process is
    gone still maps the port, and embedding that renders an empty frame."""
    monkeypatch.setattr(terminal.settings, "agent_backend", "tmux")
    monkeypatch.setattr(terminal, "_live_endpoint", lambda _t: "127.0.0.1:7681")
    monkeypatch.setattr(proxy, "probe", _reachable(False))

    _project, topic = _project_topic(client)
    token = _login(client, "alice")

    data = client.get(
        f"/api/topics/{topic['id']}/terminal",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["data"]

    assert data["available"] is False, data


def test_status_offers_the_pane_to_a_member_when_it_is_really_up(client, monkeypatch):
    """And when credential + container + live pane all hold, hand over the URL the
    iframe should load."""
    monkeypatch.setattr(terminal.settings, "agent_backend", "tmux")
    monkeypatch.setattr(terminal, "_live_endpoint", lambda _t: "127.0.0.1:7681")
    monkeypatch.setattr(proxy, "probe", _reachable(True))

    _project, topic = _project_topic(client)
    token = _login(client, "alice")

    data = client.get(
        f"/api/topics/{topic['id']}/terminal",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["data"]

    assert data["available"] is True, data
    assert data["url"] == f"/api/topics/{topic['id']}/terminal/live/"
