"""Who can open a topic's live terminal.

The status endpoint checks topic access, but the two routes that actually carry
the pane — the ttyd HTTP proxy and its WebSocket — take only a topic id. In a
default test environment they answer 404 because no container is up, which hides
the question; these tests pin it with the endpoint present.
"""

import uuid

from app.api.routes import terminal
from tests.integration.test_connector_viewer import _login


def test_the_ttyd_proxy_requires_a_credential(client, monkeypatch):
    """An unauthenticated request must not reach the pane.

    The pane shows whatever the agent is doing — file contents, command output,
    anything it echoes. A topic id is a UUID, but that is obscurity, not
    authorization.
    """
    served: list[str] = []

    class _Resp:
        content, status_code, headers = b"<ttyd/>", 200, {}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None):
            served.append(url)
            return _Resp()

    monkeypatch.setattr(terminal, "_live_endpoint", lambda _t: "127.0.0.1:7681")
    monkeypatch.setattr(terminal.httpx, "AsyncClient", lambda **kw: _Client())

    resp = client.get(f"/api/topics/{uuid.uuid4()}/terminal/live/")

    assert resp.status_code in (401, 403, 404), (
        f"unauthenticated caller got {resp.status_code} and the proxy "
        f"{'served ' + served[0] if served else 'was not reached'}"
    )
    assert not served, "the request reached ttyd without any credential"


def test_a_project_member_still_gets_the_pane(client, monkeypatch):
    """The guard must not lock out the people the feature is for."""
    served: list[str] = []

    class _Resp:
        content, status_code, headers = b"<ttyd/>", 200, {}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None):
            served.append(url)
            return _Resp()

    monkeypatch.setattr(terminal, "_live_endpoint", lambda _t: "127.0.0.1:7681")
    monkeypatch.setattr(terminal.httpx, "AsyncClient", lambda **kw: _Client())

    project = client.post(
        "/api/projects", json={"name": "T", "owner_handle": "alice"}
    ).json()["data"]
    token = _login(client, "alice")
    topic = client.post(
        "/api/topics", json={"project_id": project["id"], "title": "t"}
    ).json()["data"]

    resp = client.get(f"/api/topics/{topic['id']}/terminal/live/?token={token}")

    assert resp.status_code == 200, resp.text
    assert served, "the owner should have reached ttyd"
