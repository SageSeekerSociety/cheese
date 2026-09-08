"""Repository connection must not turn project access into GitHub access."""

from urllib.parse import parse_qs, urlparse

import pytest

from tests.integration.conftest import session_auth_headers

pytestmark = pytest.mark.usefixtures("github_binding_user")


def _project(client):
    return client.post(
        "/projects", json={"name": "Binding", "owner_handle": "alice"}
    ).json()["data"]["id"]


@pytest.mark.parametrize(
    "method,path", [("get", "connection"), ("get", "install-url"), ("post", "connect")]
)
@pytest.mark.parametrize("handle,status", [(None, 401), ("outsider", 403)])
def test_outsider_cannot_read_or_connect(client, method, path, handle, status):
    pid = _project(client)
    response = getattr(client, method)(
        f"/projects/{pid}/github/{path}",
        headers=session_auth_headers(handle) if handle else {},
    )
    assert response.status_code == status


def test_callback_selects_matching_upstream_not_first_repo(client, monkeypatch):
    from app.api.routes import github_install

    pid = _project(client)
    monkeypatch.setattr(
        github_install.ws,
        "get_upstream",
        lambda _: "https://github.com/acme/widgets.git",
    )

    async def repos(_token, _):
        return [
            {
                "full_name": "acme/wrong",
                "owner": {"login": "acme"},
                "permissions": {"push": True},
            },
            {
                "full_name": "acme/widgets",
                "owner": {"login": "acme"},
                "permissions": {"push": True},
            },
        ]

    monkeypatch.setattr(github_install, "fetch_user_installation_repos", repos)
    url = client.get(
        f"/projects/{pid}/github/install-url", headers=session_auth_headers("alice")
    ).json()["data"]["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    response = client.get(
        "/github/app/callback",
        params={"installation_id": 99, "state": state},
        follow_redirects=False,
    )
    assert "github_install=success" in response.headers["location"]
    connection = client.get(
        f"/projects/{pid}/github/connection", headers=session_auth_headers("alice")
    ).json()["data"]
    assert connection["repo"] == "acme/widgets"


def test_new_connection_requires_existing_github_account_link(client, monkeypatch):
    from app.domain.oauth.services import OAuthService

    async def no_token(self, user_id):
        return None

    monkeypatch.setattr(OAuthService, "get_github_user_token", no_token)
    pid = _project(client)
    response = client.post(
        f"/projects/{pid}/github/connect", headers=session_auth_headers("alice")
    )
    assert response.status_code == 403
    assert "GitHub 账号" in response.json()["message"]


def _state(client, pid):
    url = client.get(
        f"/projects/{pid}/github/install-url", headers=session_auth_headers("alice")
    ).json()["data"]["url"]
    return parse_qs(urlparse(url).query)["state"][0]


def _callback(client, state, installation_id=99):
    return client.get(
        "/github/app/callback",
        params={"installation_id": installation_id, "state": state},
        follow_redirects=False,
    )


def _repo(name="acme/widgets", *, push=True):
    return {
        "full_name": name,
        "owner": {"login": "acme"},
        "permissions": {"push": push},
    }


@pytest.mark.parametrize(
    "repos,upstream,reason",
    [
        ([_repo(), _repo("acme/second")], None, "repository_selection_required"),
        (
            [_repo("acme/second")],
            "https://github.com/acme/widgets",
            "upstream_not_accessible",
        ),
        ([_repo(push=False)], None, "repository_write_required"),
    ],
)
def test_callback_refuses_ambiguous_wrong_or_read_only_repo(
    client, monkeypatch, repos, upstream, reason
):
    from app.api.routes import github_install

    pid = _project(client)
    monkeypatch.setattr(github_install.ws, "get_upstream", lambda _: upstream)

    async def accessible(token, installation_id):
        assert token == "test-github-user-token"
        return repos

    monkeypatch.setattr(github_install, "fetch_user_installation_repos", accessible)
    response = _callback(client, _state(client, pid))
    assert f"reason={reason}" in response.headers["location"]
    assert not client.get(
        f"/projects/{pid}/github/connection", headers=session_auth_headers("alice")
    ).json()["data"]["connected"]


def test_callback_rejects_foreign_installation_and_spent_state(client, monkeypatch):
    from app.api.routes import github_install
    from app.domain.agent.github_app import GitHubAppError

    pid = _project(client)
    calls = []

    async def accessible(token, installation_id):
        calls.append(installation_id)
        raise GitHubAppError("GitHub refused (HTTP 404)")

    monkeypatch.setattr(github_install, "fetch_user_installation_repos", accessible)
    state = _state(client, pid)
    assert "reason=github_error" in _callback(client, state).headers["location"]
    assert "reason=invalid_state" in _callback(client, state).headers["location"]
    assert calls == [99]


def test_manager_role_is_rechecked_after_install_link_created(client, monkeypatch):
    from app.api.routes import github_install

    pid = _project(client)
    state = _state(client, pid)
    # Transfer through the real management route after the first manager left.
    client.post(
        f"/projects/{pid}/members",
        json={"user_handle": "bob", "role": "member"},
        headers=session_auth_headers("alice"),
    )
    response = client.put(
        f"/projects/{pid}/owner",
        json={"owner_handle": "bob"},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200

    async def should_not_call(*args):
        pytest.fail("GitHub accessed after initiator lost project management")

    monkeypatch.setattr(
        github_install, "fetch_user_installation_repos", should_not_call
    )
    assert "reason=access_denied" in _callback(client, state).headers["location"]


def test_failed_reconnect_preserves_existing_binding(client, monkeypatch):
    from app.api.routes import github_install
    from app.domain.agent.github_app import GitHubAppError

    pid = _project(client)

    async def accessible(token, installation_id):
        if installation_id != 99:
            raise GitHubAppError("GitHub refused (HTTP 404)")
        return [_repo()]

    monkeypatch.setattr(github_install, "fetch_user_installation_repos", accessible)
    assert (
        "github_install=success"
        in _callback(client, _state(client, pid)).headers["location"]
    )
    assert (
        "reason=github_error"
        in _callback(client, _state(client, pid), installation_id=101).headers[
            "location"
        ]
    )
    connection = client.get(
        f"/projects/{pid}/github/connection", headers=session_auth_headers("alice")
    ).json()["data"]
    assert connection == {"connected": True, "repo": "acme/widgets", "account": "acme"}


@pytest.mark.parametrize("is_agent", [False, True])
def test_github_management_follows_participant_role_and_own_account(
    client, monkeypatch, is_agent
):
    import uuid

    from app.common.auth import decode_token
    from app.core.sandbox_auth import mint_scoped_token
    from app.domain.identity.handles import topic_agent_handle
    from app.domain.oauth.services import OAuthService
    from tests.conftest import seed_user

    project = client.post(
        "/projects", json={"name": "Participant access", "owner_handle": "alice"}
    ).json()["data"]
    pid = project["id"]
    origin = project["root_topic_id"]
    handle = topic_agent_handle(uuid.UUID(origin)) if is_agent else "bob"
    user_id = int(decode_token(seed_user(client, handle))["sub"])
    auth = (
        {
            "X-Cheese-Token": mint_scoped_token(
                project_id=pid, topic_id=origin, access_scope="project"
            )
        }
        if is_agent
        else session_auth_headers(handle)
    )
    owner = session_auth_headers("alice")
    roster = f"/projects/{pid}/members"
    connection = f"/projects/{pid}/github/connection"
    install = f"/projects/{pid}/github/install-url"
    assert client.get(connection, headers=auth).status_code == 403
    assert (
        client.post(
            roster, json={"user_handle": handle, "role": "member"}, headers=owner
        ).status_code
        == 200
    )
    assert client.get(connection, headers=auth).status_code == 200
    assert client.get(install, headers=auth).status_code == 403
    assert (
        client.put(
            f"{roster}/{handle}", json={"role": "lead"}, headers=owner
        ).status_code
        == 200
    )
    assert client.get(install, headers=auth).status_code == 200

    async def no_own_account(self, requested_user_id):
        assert requested_user_id == user_id
        return None

    monkeypatch.setattr(OAuthService, "get_github_user_token", no_own_account)
    denied = client.get(install, headers=auth)
    assert denied.status_code == 403
    assert "GitHub 账号" in denied.json()["message"]
    assert (
        client.put(
            f"{roster}/{handle}", json={"role": "member"}, headers=owner
        ).status_code
        == 200
    )
    denied = client.get(install, headers=auth)
    assert denied.status_code == 403
    assert "owner / lead" in denied.json()["message"]
