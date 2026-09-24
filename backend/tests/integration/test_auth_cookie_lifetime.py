"""The sign-in cookies outlive the browser session.

A cookie without Max-Age is dropped when the browser closes (or a phone
reclaims the tab) while the access token in localStorage survives; fifteen
minutes later the refresh carries no cookie and the user is sent back to
sign in. The cookies must last as long as what they stand for.
"""

from http.cookies import SimpleCookie

from fastapi.testclient import TestClient

from app.core.config import settings
from tests.integration.conftest import CreatedUser, UserCreator

THIRTY_DAYS = 30 * 24 * 3600


def _cookies(resp) -> SimpleCookie:
    jar: SimpleCookie = SimpleCookie()
    for header in resp.headers.get_list("set-cookie"):
        jar.load(header)
    return jar


def test_signing_in_sets_a_refresh_cookie_that_lasts_as_long_as_the_token(
    user_client: UserCreator, api_client: TestClient
):
    user = user_client.create_user()

    resp = api_client.post(
        "/users/auth/login", json={"username": user.username, "password": user.password}
    )

    assert resp.status_code == 200, resp.text
    cookies = _cookies(resp)
    assert int(cookies["REFRESH_TOKEN"]["max-age"]) == (
        settings.refresh_token_expires_seconds
    )
    assert int(cookies["SESSION_ID"]["max-age"]) == THIRTY_DAYS


def test_a_refreshed_cookie_lasts_as_long_as_the_new_token(
    authenticated_user: CreatedUser, api_client: TestClient
):
    resp = api_client.post("/users/auth/refresh-token")

    assert resp.status_code == 200, resp.text
    assert int(_cookies(resp)["REFRESH_TOKEN"]["max-age"]) == (
        settings.refresh_token_expires_seconds
    )
