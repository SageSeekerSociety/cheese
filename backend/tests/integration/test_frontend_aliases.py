"""One deployment served under several origins (`app.core.origins`, #4813).

``FRONTEND_URL`` is the canonical origin and ``FRONTEND_ALIASES`` the others.
What must hold: a browser flow comes back to the origin it ran on, a sign-in
made on another origin is handed back only to an origin of this deployment,
and the platform's own gates take every one of its origins as its own.
"""

import base64
import hashlib
import secrets
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from tests.integration.conftest import UserCreator
from tests.integration.test_oauth_client_flow import (
    StateToken,
)
from tests.integration.test_oauth_client_flow import (
    state_token as state_token,
)

CANONICAL = "https://canonical.example"
ALIAS = "https://alias.example"


@pytest.fixture
def aliased(monkeypatch: pytest.MonkeyPatch) -> str:
    """The deployment at CANONICAL, also served at ALIAS."""
    monkeypatch.setattr(settings, "frontend_url", CANONICAL)
    monkeypatch.setattr(settings, "frontend_aliases", [ALIAS])
    return ALIAS


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _challenge() -> str:
    digest = hashlib.sha256(secrets.token_urlsafe(32).encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def test_an_oauth_sign_in_lands_on_the_origin_it_ran_on(
    api_client: TestClient,
    user_client: UserCreator,
    state_token: StateToken,
    aliased: str,
):
    # Binding a provider identity on the alias: the sign-in cookie it sets is
    # the alias's, so the landing page has to be there to find it.
    user = user_client.create_user()
    resp = api_client.post(
        "/users/oauth/bind",
        data={
            "stateToken": state_token("ruc", id="alias-uid"),
            "username": user.username,
            "password": user.password,
        },
        headers={"host": urlsplit(ALIAS).netloc},
        follow_redirects=False,
    )

    assert resp.status_code == 302, resp.text
    assert resp.headers["location"].startswith(f"{ALIAS}/account/oauth/success?")
    assert "cheese_refresh=" in resp.headers.get("set-cookie", "")


def test_a_request_on_an_unknown_host_lands_on_the_canonical_origin(
    api_client: TestClient, state_token: StateToken, aliased: str
):
    resp = api_client.post(
        "/users/oauth/bind",
        data={"stateToken": state_token("ruc"), "username": "x", "password": "y"},
        headers={"host": "elsewhere.example"},
        follow_redirects=False,
    )

    assert resp.status_code == 302, resp.text
    assert resp.headers["location"].startswith(f"{CANONICAL}/account/oauth/error?")


def test_a_sign_in_is_handed_back_only_to_an_origin_of_this_deployment(
    api_client: TestClient, user_client: UserCreator, aliased: str
):
    user = user_client.create_user()
    signed_in = api_client.post(
        "/users/auth/login",
        json={"username": user.username, "password": user.password},
    )
    token = signed_in.json()["data"]["accessToken"]

    for foreign in (
        "https://attacker.example",
        f"{ALIAS}.attacker.example",
        f"{ALIAS}/path",
        f"https://attacker.example#{urlsplit(ALIAS).netloc}",
    ):
        refused = api_client.post(
            "/users/auth/app-sign-in",
            headers=_bearer(token),
            json={"challenge": _challenge(), "returnTo": foreign},
        )
        assert refused.status_code == 400, (foreign, refused.text)
        assert refused.json().get("data") is None

    handed = api_client.post(
        "/users/auth/app-sign-in",
        headers=_bearer(token),
        json={"challenge": _challenge(), "returnTo": ALIAS},
    )
    assert handed.status_code == 200, handed.text
    assert handed.json()["data"]["code"]


def test_a_provider_says_which_origin_its_sign_in_runs_on(
    api_client: TestClient, aliased: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "oauth_enabled_providers", "ruc,google")
    monkeypatch.setattr(settings, "oauth_ruc_client_id", "ruc-id")
    monkeypatch.setattr(settings, "oauth_ruc_client_secret", "ruc-secret")
    monkeypatch.setattr(
        settings,
        "oauth_ruc_redirect_url",
        f"{ALIAS}/api/users/auth/oauth/callback/ruc",
    )
    # A callback on a host that is none of the deployment's origins names none.
    monkeypatch.setattr(settings, "oauth_google_client_id", "g-id")
    monkeypatch.setattr(settings, "oauth_google_client_secret", "g-secret")
    monkeypatch.setattr(
        settings,
        "oauth_google_redirect_url",
        "https://api.elsewhere.example/users/auth/oauth/callback/google",
    )

    resp = api_client.get("/users/auth/oauth/providers")

    assert resp.status_code == 200, resp.text
    origins = {p["id"]: p["origin"] for p in resp.json()["data"]["providers"]}
    assert origins == {"ruc": ALIAS, "google": None}


def test_passkeys_name_every_origin_that_may_use_them(
    api_client: TestClient, aliased: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "webauthn_origin", CANONICAL)

    resp = api_client.get("/.well-known/webauthn")

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"origins": [CANONICAL, ALIAS]}


def test_a_refresh_sent_from_an_alias_passes_the_origin_gate(
    api_client: TestClient, user_client: UserCreator, aliased: str
):
    user = user_client.create_user()
    signed_in = api_client.post(
        "/users/auth/login",
        json={"username": user.username, "password": user.password},
    )
    cookie = signed_in.cookies["cheese_refresh"]

    # An older browser: no Sec-Fetch-Site, so the Origin header decides, and
    # the request reaches the backend under a Host of its own.
    refused = api_client.post(
        "/users/auth/refresh-token",
        headers={
            "Cookie": f"cheese_refresh={cookie}",
            "Origin": "https://attacker.example",
            "host": "backend.internal",
        },
    )
    assert refused.status_code == 403, refused.text

    resp = api_client.post(
        "/users/auth/refresh-token",
        headers={
            "Cookie": f"cheese_refresh={cookie}",
            "Origin": ALIAS,
            "host": "backend.internal",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["accessToken"]
