"""Authorizing from the desktop app, finished in the browser.

Signing in: someone signed in in the browser hands that sign-in to the app
(`POST /users/auth/app-sign-in`), and the app takes it over with the secret it
kept (`.../finish`). What must hold: the code is useless without that secret
and good once, and what the app gets is a sign-in of its own.

Connecting: a flow started in the app shows its result in the app, and one
started in a browser stays there (`app/api/app_return.py`).
"""

import base64
import hashlib
import secrets
import uuid
from urllib.parse import parse_qs, urlsplit

from app.core.config import settings
from tests.conftest import seed_user
from tests.integration.conftest import UserCreator


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _secret() -> tuple[str, str]:
    """(verifier, challenge), as the app makes them."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _signed_in(client, users: UserCreator) -> tuple[str, str]:
    """Someone signed in with a password in the browser: (handle, access token)."""
    user = users.create_user()
    resp = client.post(
        "/users/auth/login",
        json={"username": user.username, "password": user.password},
    )
    assert resp.status_code == 200, resp.text
    return user.username, resp.json()["data"]["accessToken"]


def _code(client, browser: str, challenge: str) -> str:
    resp = client.post(
        "/users/auth/app-sign-in",
        headers=_bearer(browser),
        json={"challenge": challenge},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["code"]


def _finish(client, code: str, verifier: str):
    return client.post(
        "/users/auth/app-sign-in/finish", json={"code": code, "verifier": verifier}
    )


def test_the_app_takes_over_the_browsers_sign_in(api_client, user_client):
    client = api_client
    handle, browser = _signed_in(client, user_client)
    verifier, challenge = _secret()

    finished = _finish(client, _code(client, browser, challenge), verifier)
    assert finished.status_code == 200, finished.text

    refreshed = client.post(
        "/users/auth/refresh-token",
        headers={"Cookie": f"cheese_refresh={finished.cookies['cheese_refresh']}"},
    )
    assert refreshed.status_code == 200, refreshed.text
    me = client.get(
        "/users/me", headers=_bearer(refreshed.json()["data"]["accessToken"])
    )
    assert me.status_code == 200, me.text
    assert me.json()["data"]["user"]["username"] == handle


def test_a_code_is_useless_without_the_apps_secret(api_client, user_client):
    client = api_client
    _, browser = _signed_in(client, user_client)
    _, challenge = _secret()
    someone_elses, _ = _secret()

    finished = _finish(client, _code(client, browser, challenge), someone_elses)
    assert finished.status_code == 401
    assert "cheese_refresh" not in finished.cookies


def test_a_code_signs_in_once(api_client, user_client):
    client = api_client
    _, browser = _signed_in(client, user_client)
    verifier, challenge = _secret()
    code = _code(client, browser, challenge)

    assert _finish(client, code, verifier).status_code == 200
    assert _finish(client, code, verifier).status_code == 401


def test_only_someone_signed_in_can_hand_a_sign_in_over(client):
    _, challenge = _secret()
    resp = client.post("/users/auth/app-sign-in", json={"challenge": challenge})
    assert resp.status_code == 401


def _enable_github_app_provider(monkeypatch):
    monkeypatch.setattr(settings, "oauth_enabled_providers", "github_app")
    monkeypatch.setattr(settings, "oauth_github_app_client_id", "test-client-id")
    monkeypatch.setattr(settings, "oauth_github_app_client_secret", "test-secret")
    monkeypatch.setattr(
        settings, "oauth_github_app_redirect_url", "https://example.com/cb"
    )


def _link_github(client, token: str, project: uuid.UUID, *, in_app: bool) -> str:
    """Starts connecting GitHub and comes back from GitHub; where the browser lands."""
    headers = _bearer(token) | ({"X-Cheese-App": "1"} if in_app else {})
    started = client.get(
        "/users/me/github-account/authorize-url",
        params={"return_project_id": str(project)},
        headers=headers,
    )
    assert started.status_code == 200, started.text
    state = parse_qs(urlsplit(started.json()["data"]["url"]).query)["state"][0]
    # GitHub is not reached in tests, so the exchange fails: the result is an
    # error, which is shown wherever a result is.
    back = client.get(
        "/users/me/github-account/callback",
        params={"code": "x", "state": state},
        follow_redirects=False,
    )
    assert back.status_code == 302
    return back.headers["location"]


def test_connecting_from_the_app_shows_the_result_in_the_app(client, monkeypatch):
    _enable_github_app_provider(monkeypatch)
    token = seed_user(client, "dai")
    project = uuid.uuid4()

    landing = urlsplit(_link_github(client, token, project, in_app=True))
    assert landing.path == "/account/to-app"
    page = urlsplit(parse_qs(landing.query)["path"][0])
    assert page.path == f"/projects/{project}/settings"
    assert parse_qs(page.query)["github_account"] == ["error"]


def test_connecting_from_a_browser_stays_in_the_browser(client, monkeypatch):
    _enable_github_app_provider(monkeypatch)
    token = seed_user(client, "eve")
    project = uuid.uuid4()

    landing = urlsplit(_link_github(client, token, project, in_app=False))
    assert landing.path == f"/projects/{project}/settings"


def test_a_feishu_authorization_from_the_app_shows_its_result_in_the_app(client):
    back = client.get(
        "/integrations/feishu/callback",
        params={"code": "x", "state": "not-a-state.app"},
        follow_redirects=False,
    )
    landing = urlsplit(back.headers["location"])
    assert landing.path == "/account/to-app"
    page = urlsplit(parse_qs(landing.query)["path"][0])
    assert page.path == "/my/connections"
    assert "feishu" in parse_qs(page.query)


def test_a_feishu_authorization_from_a_browser_stays_there(client):
    back = client.get(
        "/integrations/feishu/callback",
        params={"code": "x", "state": "not-a-state"},
        follow_redirects=False,
    )
    assert urlsplit(back.headers["location"]).path == "/my/connections"
