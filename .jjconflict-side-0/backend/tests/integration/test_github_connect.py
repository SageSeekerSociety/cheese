"""POST /github/connect (#192 follow-up): connect via an EXISTING installation.

GitHub's ``installations/new`` page dead-ends when cheesex-app is already
installed on the org — the setup_url callback never fires and the platform
stays "尚未连接" forever. The connect route must therefore find the existing
installation itself (matching the project's upstream repo) and only hand back
the install URL when nothing matches. GitHub itself is faked at the module
seams; the tests assert what the route DOES, not HTTP details.
"""

from tests.integration.conftest import session_auth_headers


def _make_project(client) -> str:
    r = client.post("/api/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _github_world(
    monkeypatch,
    *,
    upstream: str | None,
    installations: list[dict],
    repos_by_installation: dict[int, list[dict]],
):
    """Point the connect route's GitHub + workspace seams at fakes."""
    from app.api.routes import github_install
    from app.domain.workspace import service as ws

    calls: dict[str, int] = {"list": 0, "repos": 0}

    async def _fake_list():
        calls["list"] += 1
        return installations

    async def _fake_repos(installation_id: int):
        calls["repos"] += 1
        return repos_by_installation.get(installation_id, [])

    monkeypatch.setattr(github_install, "list_app_installations", _fake_list)
    monkeypatch.setattr(github_install, "fetch_installation_repos", _fake_repos)
    monkeypatch.setattr(ws, "get_upstream", lambda pid: upstream)
    return calls


def test_connect_uses_the_existing_installation(client, monkeypatch):
    _github_world(
        monkeypatch,
        upstream="https://github.com/acme/widgets.git",
        installations=[{"id": 77}],
        repos_by_installation={
            77: [{"full_name": "acme/widgets", "owner": {"login": "acme"}}]
        },
    )
    pid = _make_project(client)

    r = client.post(
        f"/api/projects/{pid}/github/connect", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["connected"] is True
    assert data["repo"] == "acme/widgets"

    # The connection is recorded — the status endpoint sees it too.
    conn = client.get(f"/api/projects/{pid}/github/connection").json()["data"]
    assert conn == {"connected": True, "repo": "acme/widgets", "account": "acme"}


def test_connect_falls_back_to_the_install_url(client, monkeypatch):
    _github_world(
        monkeypatch,
        upstream="https://github.com/acme/widgets.git",
        installations=[],  # App not installed anywhere yet
        repos_by_installation={},
    )
    pid = _make_project(client)

    r = client.post(
        f"/api/projects/{pid}/github/connect", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["connected"] is False
    assert "/installations/new?state=" in data["install_url"]


def test_connect_skips_github_without_a_github_upstream(client, monkeypatch):
    calls = _github_world(
        monkeypatch,
        upstream="/home/repos/widgets",  # local path — not a GitHub remote
        installations=[{"id": 77}],
        repos_by_installation={},
    )
    pid = _make_project(client)

    r = client.post(
        f"/api/projects/{pid}/github/connect", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200
    assert r.json()["data"]["connected"] is False
    assert calls["list"] == 0  # never asked GitHub — nothing to match against


def test_connect_is_idempotent_once_connected(client, monkeypatch):
    calls = _github_world(
        monkeypatch,
        upstream="https://github.com/acme/widgets.git",
        installations=[{"id": 77}],
        repos_by_installation={
            77: [{"full_name": "acme/widgets", "owner": {"login": "acme"}}]
        },
    )
    pid = _make_project(client)

    first = client.post(
        f"/api/projects/{pid}/github/connect", headers=session_auth_headers("alice")
    )
    assert first.json()["data"]["connected"] is True
    second = client.post(
        f"/api/projects/{pid}/github/connect", headers=session_auth_headers("alice")
    )
    assert second.json()["data"]["connected"] is True
    assert second.json()["data"]["repo"] == "acme/widgets"
    assert calls["list"] == 1  # the second call answered from the DB, not GitHub


def test_connect_requires_auth(client, monkeypatch):
    _github_world(
        monkeypatch,
        upstream="https://github.com/acme/widgets.git",
        installations=[],
        repos_by_installation={},
    )
    pid = _make_project(client)

    r = client.post(f"/api/projects/{pid}/github/connect")
    assert r.status_code == 401
