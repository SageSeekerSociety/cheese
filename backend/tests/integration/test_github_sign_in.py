"""Signing in with GitHub creates the connection used for PR credit and repos.

The exchange with GitHub is faked; the callback, the state, the binding and
the stored, encrypted tokens are real.
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.domain.oauth.repositories import OAuthConnectionRepository
from app.domain.oauth.services import (
    GitHubProvider,
    OAuthService,
    OAuthUserInfo,
    get_github_profile_for_handle,
)
from tests.integration.conftest import post_project, session_auth_headers

_CALLBACK = "http://testserver/users/me/github-account/callback"


@pytest.fixture
def github(monkeypatch):
    monkeypatch.setattr(settings, "oauth_enabled_providers", "github_app")
    monkeypatch.setattr(settings, "oauth_github_app_client_id", "app-client-id")
    monkeypatch.setattr(settings, "oauth_github_app_client_secret", "app-secret")
    monkeypatch.setattr(settings, "oauth_github_app_redirect_url", _CALLBACK)
    tokens = {"access_token": "ghu_first", "refresh_token": "ghr_first"}
    tokens["refresh_token_expires_in"] = 15897600

    async def fake_exchange_code(self, code):
        return {**tokens, "expires_in": 28800}

    async def fake_get_user_info(self, access_token):
        return OAuthUserInfo(id="4242", email=None, name="Octo", username="octo")

    monkeypatch.setattr(GitHubProvider, "exchange_code", fake_exchange_code)
    monkeypatch.setattr(GitHubProvider, "get_user_info", fake_get_user_info)
    return tokens


def _q(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def _sign_in_with_github(client: TestClient):
    start = client.get("/users/auth/oauth/login/github_app", follow_redirects=False)
    assert start.status_code == 302, start.text
    state = _q(start.headers["location"])["state"]
    return client.get(
        "/users/me/github-account/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )


def _github_of(db_session, portal, user):
    async def read():
        found = await get_github_profile_for_handle(db_session, user.username)
        token = await OAuthService(
            OAuthConnectionRepository(db_session)
        ).get_github_user_token(user.user_id)
        return found, token

    return portal.call(read)


def test_binding_an_account_through_github_sign_in_lets_its_prs_open_as_it(
    api_client: TestClient, user_client, db_session, _portal, github
):
    user = user_client.create_user()

    back = _sign_in_with_github(api_client)
    # No account holds this GitHub identity yet: the decision page.
    assert (
        urlparse(back.headers["location"]).path == settings.frontend_oauth_complete_path
    )
    bound = api_client.post(
        "/users/oauth/bind",
        data={
            "stateToken": _q(back.headers["location"])["stateToken"],
            "username": user.username,
            "password": user.password,
        },
        follow_redirects=False,
    )
    assert _q(bound.headers["location"]).get("bound") == "true", bound.headers

    found, token = _github_of(db_session, _portal, user)
    assert found is not None
    github_id, profile = found
    assert github_id == "4242"
    # The login is what every credit and the settings page show.
    assert profile["login"] == "octo"
    assert token == "ghu_first"
    _assert_refresh_expiry(db_session, _portal, user, 15897600)


def _assert_refresh_expiry(db_session, portal, user, seconds):
    async def read():
        return await OAuthConnectionRepository(db_session).get_by_user_and_provider(
            user.user_id, "github_app"
        )

    conn = portal.call(read)
    assert conn.refresh_token_expires is not None
    assert (
        datetime.now(UTC) + timedelta(seconds=seconds - 60)
        < conn.refresh_token_expires
        < datetime.now(UTC) + timedelta(seconds=seconds + 60)
    )


def test_github_sign_in_alone_can_connect_a_repository(
    api_client: TestClient, user_client, github, monkeypatch
):
    from app.api.routes import github_install

    user = user_client.create_user()
    back = _sign_in_with_github(api_client)
    bound = api_client.post(
        "/users/oauth/bind",
        data={
            "stateToken": _q(back.headers["location"])["stateToken"],
            "username": user.username,
            "password": user.password,
        },
        follow_redirects=False,
    )
    assert _q(bound.headers["location"]).get("bound") == "true"

    project = post_project(
        api_client,
        json={"name": "GitHub sign-in repo", "forge_kind": "github_app"},
        owner=user.username,
    ).json()["data"]["id"]
    headers = session_auth_headers(user.username)
    upstream = api_client.put(
        f"/projects/{project}/upstream",
        json={"url": "https://github.com/acme/widgets"},
        headers=headers,
    )
    assert upstream.status_code == 200, upstream.text

    async def installations(token):
        assert token == "ghu_first"
        return [{"id": 77}]

    async def repos(token, installation_id):
        assert (token, installation_id) == ("ghu_first", 77)
        return [
            {
                "full_name": "acme/widgets",
                "owner": {"login": "acme"},
                "permissions": {"push": True},
            }
        ]

    monkeypatch.setattr(github_install, "list_user_installations", installations)
    monkeypatch.setattr(github_install, "fetch_user_installation_repos", repos)
    connected = api_client.post(f"/projects/{project}/github/connect", headers=headers)
    assert connected.status_code == 200, connected.text
    assert connected.json()["data"]["repo"] == "acme/widgets"


def test_signing_in_again_renews_the_stored_authorization(
    api_client: TestClient, user_client, db_session, _portal, github
):
    user = user_client.create_user()
    back = _sign_in_with_github(api_client)
    api_client.post(
        "/users/oauth/bind",
        data={
            "stateToken": _q(back.headers["location"])["stateToken"],
            "username": user.username,
            "password": user.password,
        },
        follow_redirects=False,
    )
    api_client.cookies.clear()
    github["access_token"] = "ghu_second"
    github["refresh_token_expires_in"] = 3600

    again = _sign_in_with_github(api_client)

    assert (
        urlparse(again.headers["location"]).path == settings.frontend_oauth_success_path
    )
    _found, token = _github_of(db_session, _portal, user)
    assert token == "ghu_second"
    _assert_refresh_expiry(db_session, _portal, user, 3600)


def test_a_sign_in_state_without_its_cookie_is_refused(
    api_client: TestClient, user_client, db_session, _portal, github
):
    """A sign-in state sent without its browser cookie is refused."""
    user = user_client.create_user()
    start = api_client.get("/users/auth/oauth/login/github_app", follow_redirects=False)
    state = _q(start.headers["location"])["state"]
    api_client.cookies.clear()

    back = api_client.get(
        "/users/me/github-account/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )

    assert urlparse(back.headers["location"]).path == settings.frontend_oauth_error_path
    found, _token = _github_of(db_session, _portal, user)
    assert found is None
