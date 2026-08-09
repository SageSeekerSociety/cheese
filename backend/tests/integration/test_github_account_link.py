"""Connect a human's own GitHub account via cheesex-app (#192, user-to-server).

Only the parts that don't require a real GitHub OAuth exchange: auth gating,
state verification, and the redirect shape. The token-exchange path itself
is exercised at the unit level (app.core.github_install_state) and via
GitHubProvider, which is shared with the "github" login provider.
"""

import uuid

from app.core.github_install_state import mint_account_link_state
from tests.conftest import seed_user


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_authorize_url_requires_login(client):
    r = client.get("/api/users/me/github-account/authorize-url")
    assert r.status_code == 401


def test_authorize_url_404s_when_provider_not_configured(client):
    # oauth_enabled_providers doesn't include "github_app" in the test env, so
    # the generic OAuthService reports it as an unregistered provider — same
    # behavior as any other disabled OAuth provider, not a #192-specific 500.
    token = seed_user(client, "alice")
    r = client.get("/api/users/me/github-account/authorize-url", headers=_bearer(token))
    assert r.status_code == 404


def test_callback_garbage_state_redirects_to_root(client):
    r = client.get(
        "/api/users/me/github-account/callback",
        params={"code": "x", "state": "not-a-jwt"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "github_account=error" in r.headers["location"]
    assert "reason=invalid_state" in r.headers["location"]


def test_callback_returns_to_the_originating_project(client):
    pid = uuid.uuid4()
    state = mint_account_link_state(1, return_project_id=pid)
    # No oauth provider configured → the exchange itself fails, but the
    # redirect must still land on the project that asked, not the root.
    r = client.get(
        "/api/users/me/github-account/callback",
        params={"code": "x", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert f"/project/{pid}/settings" in r.headers["location"]
    assert "github_account=error" in r.headers["location"]
