"""POST /github/connect (#192 follow-up): connect via an EXISTING installation.

GitHub's ``installations/new`` page dead-ends when cheesex-app is already
installed on the org — the setup_url callback never fires and the platform
stays "尚未连接" forever. The connect route must therefore find the existing
installation itself (matching the project's upstream repo) and only hand back
the install URL when nothing matches. GitHub itself is faked at the module
seams; the tests assert what the route DOES, not HTTP details.
"""

from unittest.mock import AsyncMock

import pytest

from tests.integration.conftest import post_project, session_auth_headers

pytestmark = pytest.mark.usefixtures("github_binding_user")


def _make_project(client) -> str:
    r = post_project(
        client,
        json={"name": "P", "owner_handle": "alice", "forge_kind": "github_app"},
    )
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
    from app.domain.review.github_pr import parse_github_repo

    calls: dict[str, int] = {"list": 0, "repos": 0}

    async def _fake_list(_token):
        calls["list"] += 1
        return installations

    async def _fake_repos(_token, installation_id: int):
        calls["repos"] += 1
        return repos_by_installation.get(installation_id, [])

    monkeypatch.setattr(github_install, "list_user_installations", _fake_list)
    monkeypatch.setattr(github_install, "fetch_user_installation_repos", _fake_repos)
    parsed = parse_github_repo(upstream) if upstream else None
    monkeypatch.setattr(
        github_install,
        "_upstream_repo",
        AsyncMock(return_value="/".join(parsed) if parsed else None),
    )
    return calls


def test_connect_uses_the_existing_installation(client, monkeypatch):
    _github_world(
        monkeypatch,
        upstream="https://github.com/acme/widgets.git",
        installations=[{"id": 77}],
        repos_by_installation={
            77: [
                {
                    "full_name": "acme/widgets",
                    "owner": {"login": "acme"},
                    "permissions": {"push": True},
                }
            ]
        },
    )
    pid = _make_project(client)

    r = client.post(
        f"/projects/{pid}/github/connect", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["connected"] is True
    assert data["repo"] == "acme/widgets"

    # The connection is recorded — the status endpoint sees it too.
    conn = client.get(
        f"/projects/{pid}/github/connection", headers=session_auth_headers("alice")
    ).json()["data"]
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
        f"/projects/{pid}/github/connect", headers=session_auth_headers("alice")
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
        f"/projects/{pid}/github/connect", headers=session_auth_headers("alice")
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
            77: [
                {
                    "full_name": "acme/widgets",
                    "owner": {"login": "acme"},
                    "permissions": {"push": True},
                }
            ]
        },
    )
    pid = _make_project(client)

    first = client.post(
        f"/projects/{pid}/github/connect", headers=session_auth_headers("alice")
    )
    assert first.json()["data"]["connected"] is True
    second = client.post(
        f"/projects/{pid}/github/connect", headers=session_auth_headers("alice")
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

    r = client.post(f"/projects/{pid}/github/connect")
    assert r.status_code == 401


_WIDGETS = {
    "full_name": "acme/widgets",
    "owner": {"login": "acme"},
    "permissions": {"push": True},
}


def _project_of(client, owner: str, name: str) -> str:
    r = post_project(
        client,
        json={"name": name, "owner_handle": owner, "forge_kind": "github_app"},
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def test_connect_names_the_project_already_holding_the_repo(client, monkeypatch):
    _github_world(
        monkeypatch,
        upstream="https://github.com/acme/widgets.git",
        installations=[{"id": 77}],
        repos_by_installation={77: [_WIDGETS]},
    )
    first = _project_of(client, "alice", "Widgets main")
    second = _project_of(client, "alice", "Widgets again")
    headers = session_auth_headers("alice")
    assert client.post(f"/projects/{first}/github/connect", headers=headers).json()[
        "data"
    ]["connected"]

    r = client.post(f"/projects/{second}/github/connect", headers=headers)

    assert r.status_code == 409
    message = r.json()["error"]["message"]
    assert "acme/widgets" in message
    assert "「Widgets main」" in message
    assert "installation" not in message
    assert client.get(f"/projects/{second}/github/connection", headers=headers).json()[
        "data"
    ] == {"connected": False}


def test_connect_does_not_name_a_project_the_caller_cannot_see(client, monkeypatch):
    _github_world(
        monkeypatch,
        upstream="https://github.com/acme/widgets.git",
        installations=[{"id": 77}],
        repos_by_installation={77: [_WIDGETS]},
    )
    alices = _project_of(client, "alice", "Private widgets")
    bobs = _project_of(client, "bob", "Bob widgets")
    assert client.post(
        f"/projects/{alices}/github/connect", headers=session_auth_headers("alice")
    ).json()["data"]["connected"]

    r = client.post(
        f"/projects/{bobs}/github/connect", headers=session_auth_headers("bob")
    )

    assert r.status_code == 409
    message = r.json()["error"]["message"]
    assert "acme/widgets" in message
    assert "Private widgets" not in message


def test_connect_shares_one_installation_across_repos(client, monkeypatch):
    world = {
        77: [
            _WIDGETS,
            {
                "full_name": "acme/gadgets",
                "owner": {"login": "acme"},
                "permissions": {"push": True},
            },
        ]
    }
    headers = session_auth_headers("alice")
    for name in ("widgets", "gadgets"):
        _github_world(
            monkeypatch,
            upstream=f"https://github.com/acme/{name}.git",
            installations=[{"id": 77}],
            repos_by_installation=world,
        )
        pid = _project_of(client, "alice", name)
        r = client.post(f"/projects/{pid}/github/connect", headers=headers)
        assert r.status_code == 200
        assert r.json()["data"]["repo"] == f"acme/{name}"
