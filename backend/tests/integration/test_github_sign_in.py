"""Signing in with GitHub creates the connection used for PR credit and repos.

The exchange with GitHub is faked; the callback, the state, the binding and
the stored, encrypted tokens are real.
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.common.auth import create_access_token
from app.core.config import settings
from app.domain.oauth.repositories import OAuthConnectionRepository
from app.domain.oauth.services import (
    GitHubProvider,
    OAuthService,
    OAuthUserInfo,
    get_github_profile_for_handle,
)
from app.domain.user.models import User
from app.domain.user.sessions import SessionService
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


def _sign_in_with_github(client: TestClient, *, intent: str | None = None):
    start = client.get(
        "/users/auth/oauth/login/github_app",
        params={"intent": intent} if intent else None,
        follow_redirects=False,
    )
    assert start.status_code == 302, start.text
    state = _q(start.headers["location"])["state"]
    return client.get(
        "/users/me/github-account/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )


_RETURN_TO = "/projects/acme/settings"


def _intent_for(client: TestClient, user, *, in_app: bool = False) -> str:
    """What the app asks for, as the signed-in ``user``, before it opens GitHub."""
    headers = {
        "Authorization": "Bearer "
        + create_access_token(user.user_id, handle=user.username)
    }
    if in_app:
        headers["X-Cheese-App"] = "1"
    response = client.post(
        "/users/auth/oauth/github-app/intent",
        json={"returnPath": _RETURN_TO},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["intent"]


def _browser_signed_in_as(client: TestClient, db_session, portal, user) -> None:
    """This browser holds a sign-in of ``user``, whatever way they signed in."""

    async def start():
        started = await SessionService(db_session).start(
            user.user_id, "email_code", ip="127.0.0.1", user_agent="pytest"
        )
        await db_session.flush()
        return started.refresh_token

    client.cookies.clear()
    client.cookies.set("cheese_refresh", portal.call(start))


def _signs_nobody_in(*responses) -> bool:
    return all(
        "cheese_refresh=" not in h
        for r in responses
        for h in r.headers.get_list("set-cookie")
    )


def _drop_password(db_session, portal, user) -> None:
    async def drop():
        await db_session.execute(
            update(User).where(User.id == user.user_id).values(hashed_password=None)
        )
        await db_session.flush()

    portal.call(drop)


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
    _browser_signed_in_as(api_client, db_session, _portal, user)
    github["access_token"] = "ghu_second"
    github["refresh_token_expires_in"] = 3600

    again = _sign_in_with_github(api_client, intent=_intent_for(api_client, user))

    assert urlparse(again.headers["location"]).path == _RETURN_TO
    assert _signs_nobody_in(again)
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


def test_existing_account_without_password_gets_github_setup_guidance(
    api_client: TestClient, user_client, db_session, _portal, github
):
    user = user_client.create_user()
    _drop_password(db_session, _portal, user)
    back = _sign_in_with_github(api_client)
    bound = api_client.post(
        "/users/oauth/bind",
        data={
            "stateToken": _q(back.headers["location"])["stateToken"],
            "username": user.username,
            "password": "anything",
        },
        follow_redirects=False,
    )
    assert _q(bound.headers["location"])["error_code"] == (
        "GITHUB_PASSWORD_VERIFICATION_FAILED"
    )
    assert _github_of(db_session, _portal, user)[0] is None


def test_matching_email_without_password_gets_the_same_github_guidance(
    api_client: TestClient, user_client, db_session, _portal, github, monkeypatch
):
    user = user_client.create_user()
    _drop_password(db_session, _portal, user)

    async def github_with_same_email(self, access_token):
        return OAuthUserInfo(id="4242", email=user.email, name="Octo", username="octo")

    monkeypatch.setattr(GitHubProvider, "get_user_info", github_with_same_email)
    back = _sign_in_with_github(api_client)
    assert (
        urlparse(back.headers["location"]).path == settings.frontend_oauth_verify_path
    )
    verified = api_client.post(
        "/users/auth/oauth/verify",
        json={
            "sessionId": _q(back.headers["location"])["sessionId"],
            "password": "anything",
        },
        follow_redirects=False,
    )
    assert _q(verified.headers["location"])["error_code"] == (
        "GITHUB_PASSWORD_VERIFICATION_FAILED"
    )
    assert _github_of(db_session, _portal, user)[0] is None


def test_reauthorization_from_another_cheese_account_cannot_renew_its_github_token(
    api_client: TestClient, user_client, db_session, _portal, github, monkeypatch
):
    account_a = user_client.create_user()
    account_b = user_client.create_user()
    back = _sign_in_with_github(api_client)
    bound = api_client.post(
        "/users/oauth/bind",
        data={
            "stateToken": _q(back.headers["location"])["stateToken"],
            "username": account_a.username,
            "password": account_a.password,
        },
        follow_redirects=False,
    )
    assert _q(bound.headers["location"]).get("bound") == "true"

    async def other_github_identity(self, access_token):
        return OAuthUserInfo(id="other", email=None, name="Other", username="other")

    monkeypatch.setattr(GitHubProvider, "get_user_info", other_github_identity)
    api_client.cookies.clear()
    github["access_token"] = "ghu_b"
    back = _sign_in_with_github(api_client)
    bound = api_client.post(
        "/users/oauth/bind",
        data={
            "stateToken": _q(back.headers["location"])["stateToken"],
            "username": account_b.username,
            "password": account_b.password,
        },
        follow_redirects=False,
    )
    assert _q(bound.headers["location"]).get("bound") == "true"

    _browser_signed_in_as(api_client, db_session, _portal, account_a)
    intent = _intent_for(api_client, account_a)

    # The app belongs to A, but the system browser authorizes B's GitHub.
    github["access_token"] = "ghu_b_new"
    start = api_client.get(
        "/users/auth/oauth/login/github_app",
        params={"intent": intent},
        follow_redirects=False,
    )
    state = _q(start.headers["location"])["state"]
    result = api_client.get(
        "/users/me/github-account/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )
    assert _q(result.headers["location"])["error_code"] == "ACCOUNT_MISMATCH"
    assert "cheese_refresh" not in result.headers.get("set-cookie", "")
    assert _github_of(db_session, _portal, account_b)[1] == "ghu_b"
    assert _github_of(db_session, _portal, account_a)[1] == "ghu_first"


def test_a_signed_in_account_without_a_password_connects_github_from_the_app(
    api_client: TestClient, user_client, db_session, _portal, github
):
    # Signed in by email code or RUC auth: there is no password to prove.
    user = user_client.create_user()
    _drop_password(db_session, _portal, user)
    _browser_signed_in_as(api_client, db_session, _portal, user)

    back = _sign_in_with_github(api_client, intent=_intent_for(api_client, user))

    location = back.headers["location"]
    assert urlparse(location).path == _RETURN_TO, location
    # A connection, not a sign-in: the browser keeps the session it had.
    assert _signs_nobody_in(back)
    found, token = _github_of(db_session, _portal, user)
    assert found is not None and found[0] == "4242"
    assert found[1]["login"] == "octo"
    assert token == "ghu_first"


def test_connecting_from_the_app_refuses_a_github_identity_bound_to_another_account(
    api_client: TestClient, user_client, db_session, _portal, github
):
    owner = user_client.create_user()
    back = _sign_in_with_github(api_client)
    bound = api_client.post(
        "/users/oauth/bind",
        data={
            "stateToken": _q(back.headers["location"])["stateToken"],
            "username": owner.username,
            "password": owner.password,
        },
        follow_redirects=False,
    )
    assert _q(bound.headers["location"]).get("bound") == "true"
    other = user_client.create_user()
    _browser_signed_in_as(api_client, db_session, _portal, other)
    github["access_token"] = "ghu_other"
    back = _sign_in_with_github(api_client, intent=_intent_for(api_client, other))

    assert _q(back.headers["location"])["error_code"] == "ALREADY_LINKED"
    assert "cheese_refresh" not in back.headers.get("set-cookie", "")
    assert _github_of(db_session, _portal, other)[0] is None
    assert _github_of(db_session, _portal, owner)[1] == "ghu_first"


def test_an_app_intent_starts_one_authorization_only(
    api_client: TestClient, user_client, db_session, _portal, github
):
    user = user_client.create_user()
    _browser_signed_in_as(api_client, db_session, _portal, user)
    intent = _intent_for(api_client, user)
    first = api_client.get(
        "/users/auth/oauth/login/github_app",
        params={"intent": intent},
        follow_redirects=False,
    )
    assert urlparse(first.headers["location"]).netloc == "github.com"

    second = api_client.get(
        "/users/auth/oauth/login/github_app",
        params={"intent": intent},
        follow_redirects=False,
    )
    assert urlparse(second.headers["location"]).path == (
        settings.frontend_oauth_error_path
    )
    assert _github_of(db_session, _portal, user)[0] is None


def test_an_intent_opened_in_another_browser_connects_nothing_and_signs_no_one_in(
    api_client: TestClient, user_client, db_session, _portal, github
):
    # M mints an intent and sends the link on. Whoever opens it, signed out or
    # signed in as somebody else, must neither attach their GitHub to M nor
    # come away signed in as M.
    minter = user_client.create_user()
    clicker = user_client.create_user()
    for browser in ("signed out", "signed in as someone else"):
        api_client.cookies.clear()
        intent = _intent_for(api_client, minter)
        if browser != "signed out":
            _browser_signed_in_as(api_client, db_session, _portal, clicker)

        start = api_client.get(
            "/users/auth/oauth/login/github_app",
            params={"intent": intent},
            follow_redirects=False,
        )

        location = start.headers["location"]
        assert urlparse(location).path == settings.frontend_oauth_error_path, (
            browser,
            location,
        )
        assert _q(location)["error_code"] == "GITHUB_CONNECT_SIGNED_OUT"
        assert _signs_nobody_in(start)
        assert _github_of(db_session, _portal, minter)[0] is None
        assert _github_of(db_session, _portal, clicker)[0] is None


def test_connecting_from_the_desktop_app_shows_the_result_in_the_app(
    api_client: TestClient, user_client, db_session, _portal, github
):
    user = user_client.create_user()
    _browser_signed_in_as(api_client, db_session, _portal, user)

    back = _sign_in_with_github(
        api_client, intent=_intent_for(api_client, user, in_app=True)
    )

    landing = urlparse(back.headers["location"])
    assert landing.path == "/account/to-app", back.headers["location"]
    assert _q(back.headers["location"])["path"] == _RETURN_TO
    assert _signs_nobody_in(back)
    assert _github_of(db_session, _portal, user)[1] == "ghu_first"


def test_an_account_with_github_connected_cannot_add_a_second_identity(
    api_client: TestClient, user_client, db_session, _portal, github, monkeypatch
):
    user = user_client.create_user()
    _browser_signed_in_as(api_client, db_session, _portal, user)
    _sign_in_with_github(api_client, intent=_intent_for(api_client, user))
    assert _github_of(db_session, _portal, user)[0][0] == "4242"

    async def unclaimed_identity(self, access_token):
        return OAuthUserInfo(id="5151", email=None, name="New", username="new")

    monkeypatch.setattr(GitHubProvider, "get_user_info", unclaimed_identity)
    github["access_token"] = "ghu_new"
    back = _sign_in_with_github(api_client, intent=_intent_for(api_client, user))

    assert _q(back.headers["location"])["error_code"] == "ACCOUNT_MISMATCH"
    found, token = _github_of(db_session, _portal, user)
    assert found[0] == "4242" and token == "ghu_first"
