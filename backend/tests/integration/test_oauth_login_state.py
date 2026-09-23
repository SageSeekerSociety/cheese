"""The OAuth login `state`: issued by the server, bound to the browser that
started the flow, and accepted by the callback exactly once."""

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.domain.oauth.services import GitHubProvider, OAuthUserInfo

# The provider sends the browser here, so this is where the state cookie must
# be readable. It matches the TestClient's own origin, which lets the client's
# cookie jar behave the way a browser's would.
_REDIRECT_URL = "http://testserver/users/auth/oauth/callback/github"


@pytest.fixture
def github(monkeypatch) -> list[str]:
    """Enable the github provider with a fake exchange; returns the codes the
    provider was asked to exchange."""
    monkeypatch.setattr(settings, "oauth_enabled_providers", "github")
    monkeypatch.setattr(settings, "oauth_github_client_id", "test-client-id")
    monkeypatch.setattr(settings, "oauth_github_client_secret", "test-secret")
    monkeypatch.setattr(settings, "oauth_github_redirect_url", _REDIRECT_URL)
    exchanged: list[str] = []

    async def fake_exchange_code(self, code):
        exchanged.append(code)
        return {"access_token": "gh-token"}

    async def fake_get_user_info(self, access_token):
        return OAuthUserInfo(id="gh-state-uid", email=None, name="G")

    monkeypatch.setattr(GitHubProvider, "exchange_code", fake_exchange_code)
    monkeypatch.setattr(GitHubProvider, "get_user_info", fake_get_user_info)
    return exchanged


def _start_login(client: TestClient) -> str:
    """Begin a login the way the sign-in page does; returns the state the
    provider was handed."""
    resp = client.get(
        "/users/auth/oauth/login/github",
        params={"state": "chosen-by-the-client"},
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    location = resp.headers["location"]
    assert location.startswith("https://github.com/login/oauth/authorize?")
    return parse_qs(urlparse(location).query)["state"][0]


def _callback(client: TestClient, **params):
    return client.get(
        "/users/auth/oauth/callback/github", params=params, follow_redirects=False
    )


def _rejected(resp) -> bool:
    assert resp.status_code == 302, resp.text
    return resp.headers["location"].startswith(
        f"{settings.frontend_url}{settings.frontend_oauth_error_path}"
    )


def test_the_login_state_is_issued_by_the_server(api_client: TestClient, github):
    state = _start_login(api_client)
    assert state != "chosen-by-the-client"


def test_the_state_cookie_is_scoped_to_the_callback(api_client: TestClient, github):
    resp = api_client.get("/users/auth/oauth/login/github", follow_redirects=False)
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert f"path={urlparse(_REDIRECT_URL).path}" in cookie


def test_a_login_started_in_this_browser_completes(api_client: TestClient, github):
    state = _start_login(api_client)

    resp = _callback(api_client, code="c1", state=state)

    assert not _rejected(resp)
    assert github == ["c1"]


def test_a_callback_without_state_is_refused(api_client: TestClient, github):
    _start_login(api_client)

    assert _rejected(_callback(api_client, code="c1"))
    assert github == []


def test_a_callback_from_another_browser_is_refused(api_client: TestClient, github):
    state = _start_login(api_client)
    api_client.cookies.clear()

    assert _rejected(_callback(api_client, code="c1", state=state))
    assert github == []


def test_a_state_that_does_not_match_the_cookie_is_refused(
    api_client: TestClient, github
):
    _start_login(api_client)

    assert _rejected(_callback(api_client, code="c1", state="forged-state"))
    assert github == []


def test_a_state_is_accepted_once(api_client: TestClient, github):
    state = _start_login(api_client)
    assert not _rejected(_callback(api_client, code="c1", state=state))

    assert _rejected(_callback(api_client, code="c2", state=state))
    assert github == ["c1"]


def test_an_unknown_provider_is_not_found(api_client: TestClient, github):
    resp = api_client.get("/users/auth/oauth/login/nope", follow_redirects=False)
    assert resp.status_code == 404
