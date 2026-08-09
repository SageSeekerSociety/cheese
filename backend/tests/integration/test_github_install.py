"""GitHub App install flow (#192): connect a project to a repo.

fetch_installation_repos (a real GitHub API call) is monkeypatched — these
tests exercise the state verification, upsert/conflict, and redirect shape,
not GitHub's API itself.
"""

import uuid

from app.core.github_install_state import mint_install_state


def _project(client, name: str = "P") -> str:
    return client.post(
        "/api/projects", json={"name": name, "owner_handle": "alice"}
    ).json()["data"]["id"]


def _stub_repos(monkeypatch, repos: list[dict]) -> None:
    async def _fake(_installation_id: int) -> list[dict]:
        return repos

    monkeypatch.setattr("app.api.routes.github_install.fetch_installation_repos", _fake)


_ONE_REPO = [{"full_name": "acme/widgets", "owner": {"login": "acme"}}]


def test_connection_unset_by_default(client):
    pid = _project(client)
    r = client.get(f"/api/projects/{pid}/github/connection")
    assert r.status_code == 200
    assert r.json()["data"] == {"connected": False}


def test_install_url_carries_a_state_for_this_project(client):
    pid = _project(client)
    r = client.get(f"/api/projects/{pid}/github/install-url")
    assert r.status_code == 200
    url = r.json()["data"]["url"]
    assert url.startswith("https://github.com/apps/")
    assert "installations/new?state=" in url


def test_install_url_404s_for_unknown_project(client):
    r = client.get(f"/api/projects/{uuid.uuid4()}/github/install-url")
    assert r.status_code == 404


def test_callback_garbage_state_rejected(client):
    r = client.get(
        "/github/app/callback",
        params={"installation_id": 1, "setup_action": "install", "state": "not-a-jwt"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "github_install=error" in r.headers["location"]
    assert "reason=invalid_state" in r.headers["location"]


def test_callback_missing_state_rejected(client):
    r = client.get(
        "/github/app/callback",
        params={"installation_id": 1, "setup_action": "install"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "reason=invalid_state" in r.headers["location"]


def test_callback_setup_action_request_is_pending_not_an_error(client):
    pid = _project(client)
    state = mint_install_state(uuid.UUID(pid))
    r = client.get(
        "/github/app/callback",
        params={"setup_action": "request", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert f"/project/{pid}/settings" in r.headers["location"]
    assert "github_install=pending" in r.headers["location"]
    # Nothing got connected — this is just "an admin still has to approve".
    assert not client.get(f"/api/projects/{pid}/github/connection").json()["data"][
        "connected"
    ]


def test_callback_missing_installation_id_rejected(client):
    pid = _project(client)
    state = mint_install_state(uuid.UUID(pid))
    r = client.get(
        "/github/app/callback",
        params={"setup_action": "install", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "reason=missing_installation_id" in r.headers["location"]


def test_callback_success_connects_the_repo(client, monkeypatch):
    _stub_repos(monkeypatch, _ONE_REPO)
    pid = _project(client)
    state = mint_install_state(uuid.UUID(pid))
    r = client.get(
        "/github/app/callback",
        params={"installation_id": 999, "setup_action": "install", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    location = r.headers["location"]
    assert f"/project/{pid}/settings" in location
    assert "github_install=success" in location

    conn = client.get(f"/api/projects/{pid}/github/connection").json()["data"]
    assert conn == {"connected": True, "repo": "acme/widgets", "account": "acme"}


def test_callback_no_accessible_repos_rejected(client, monkeypatch):
    _stub_repos(monkeypatch, [])
    pid = _project(client)
    state = mint_install_state(uuid.UUID(pid))
    r = client.get(
        "/github/app/callback",
        params={"installation_id": 999, "setup_action": "install", "state": state},
        follow_redirects=False,
    )
    assert "reason=no_accessible_repos" in r.headers["location"]
    assert not client.get(f"/api/projects/{pid}/github/connection").json()["data"][
        "connected"
    ]


def test_callback_reconnect_same_project_updates_in_place(client, monkeypatch):
    """Installing again for the same project (a different repo picked this
    time) replaces the old connection rather than conflicting with itself."""
    pid = _project(client)
    state = mint_install_state(uuid.UUID(pid))

    _stub_repos(monkeypatch, _ONE_REPO)
    client.get(
        "/github/app/callback",
        params={"installation_id": 999, "setup_action": "install", "state": state},
        follow_redirects=False,
    )

    _stub_repos(monkeypatch, [{"full_name": "acme/other", "owner": {"login": "acme"}}])
    r = client.get(
        "/github/app/callback",
        params={"installation_id": 999, "setup_action": "install", "state": state},
        follow_redirects=False,
    )
    assert "github_install=success" in r.headers["location"]
    conn = client.get(f"/api/projects/{pid}/github/connection").json()["data"]
    assert conn["repo"] == "acme/other"


def test_callback_installation_conflict_with_another_project(client, monkeypatch):
    """The same GitHub-side installation_id can't end up bound to two
    different platform projects — whichever token got minted for it would be
    ambiguous about whose git operations it's for."""
    _stub_repos(monkeypatch, _ONE_REPO)
    pid_a = _project(client, "A")
    pid_b = _project(client, "B")

    r_a = client.get(
        "/github/app/callback",
        params={
            "installation_id": 555,
            "setup_action": "install",
            "state": mint_install_state(uuid.UUID(pid_a)),
        },
        follow_redirects=False,
    )
    assert "github_install=success" in r_a.headers["location"]

    r_b = client.get(
        "/github/app/callback",
        params={
            "installation_id": 555,
            "setup_action": "install",
            "state": mint_install_state(uuid.UUID(pid_b)),
        },
        follow_redirects=False,
    )
    assert f"/project/{pid_b}/settings" in r_b.headers["location"]
    assert "reason=installation_conflict" in r_b.headers["location"]

    # Project A's connection is untouched by B's rejected attempt.
    conn_a = client.get(f"/api/projects/{pid_a}/github/connection").json()["data"]
    assert conn_a == {"connected": True, "repo": "acme/widgets", "account": "acme"}
    conn_b = client.get(f"/api/projects/{pid_b}/github/connection").json()["data"]
    assert conn_b == {"connected": False}


def test_callback_github_error_does_not_connect(client, monkeypatch):
    from app.domain.agent.github_app import GitHubAppError

    async def _boom(_installation_id: int) -> list[dict]:
        raise GitHubAppError("GitHub said no")

    monkeypatch.setattr("app.api.routes.github_install.fetch_installation_repos", _boom)
    pid = _project(client)
    state = mint_install_state(uuid.UUID(pid))
    r = client.get(
        "/github/app/callback",
        params={"installation_id": 999, "setup_action": "install", "state": state},
        follow_redirects=False,
    )
    assert "reason=github_error" in r.headers["location"]
    assert not client.get(f"/api/projects/{pid}/github/connection").json()["data"][
        "connected"
    ]
