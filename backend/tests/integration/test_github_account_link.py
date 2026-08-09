"""Connect a human's own GitHub account via cheesex-app (#192, user-to-server).

Only the parts that don't require a real GitHub OAuth exchange: auth gating,
state verification, and the redirect shape. The token-exchange path itself
is exercised at the unit level (app.core.github_install_state) and via
GitHubProvider, which is shared with the "github" login provider.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.common.auth import decode_token
from app.core.config import settings
from app.core.crypto import decrypt_text, encrypt_text
from app.core.github_install_state import mint_account_link_state
from app.domain.oauth.models import UserOAuthConnection
from app.domain.oauth.services import GitHubProvider, OAuthProviderConfig, OAuthUserInfo
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


def _enable_github_app_provider(monkeypatch):
    monkeypatch.setattr(settings, "oauth_enabled_providers", "github_app")
    monkeypatch.setattr(settings, "oauth_github_app_client_id", "test-client-id")
    monkeypatch.setattr(settings, "oauth_github_app_client_secret", "test-secret")
    monkeypatch.setattr(
        settings, "oauth_github_app_redirect_url", "https://example.com/cb"
    )


def _fetch_connection(client, user_id: int) -> UserOAuthConnection:
    async def _fetch() -> UserOAuthConnection:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            result = await session.execute(
                select(UserOAuthConnection).where(
                    UserOAuthConnection.user_id == user_id,
                    UserOAuthConnection.provider_id == "github_app",
                )
            )
            return result.scalar_one()

    return asyncio.run(_fetch())


class TestAccountLinkTokenPersistence:
    """#192 gap: the callback used to discard the exchanged access_token.
    These exercise the real DB round trip (real Postgres, real migration
    column, real Fernet encryption) rather than mocking the repository."""

    def test_callback_persists_encrypted_access_and_refresh_token(
        self, client, monkeypatch
    ):
        _enable_github_app_provider(monkeypatch)

        async def fake_exchange_code(self, code):
            return {
                "access_token": "gh-live-token",
                "expires_in": 28800,
                "refresh_token": "gh-refresh-token",
            }

        async def fake_get_user_info(self, access_token):
            return OAuthUserInfo(id="gh-uid-1", email="a@example.com", name="A")

        monkeypatch.setattr(GitHubProvider, "exchange_code", fake_exchange_code)
        monkeypatch.setattr(GitHubProvider, "get_user_info", fake_get_user_info)

        token = seed_user(client, "bob_ghlink")
        user_id = int(decode_token(token)["sub"])
        state = mint_account_link_state(user_id, return_project_id=None)

        r = client.get(
            "/api/users/me/github-account/callback",
            params={"code": "x", "state": state},
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "github_account=success" in r.headers["location"]

        conn = _fetch_connection(client, user_id)
        assert conn.access_token is not None
        assert conn.access_token != "gh-live-token"  # never stored in plaintext
        assert decrypt_text(conn.access_token) == "gh-live-token"
        assert conn.refresh_token is not None
        assert decrypt_text(conn.refresh_token) == "gh-refresh-token"
        assert conn.token_expires is not None

    def test_relink_updates_existing_connection_in_place(self, client, monkeypatch):
        _enable_github_app_provider(monkeypatch)

        async def fake_get_user_info(self, access_token):
            return OAuthUserInfo(id="gh-uid-2", email="c@example.com", name="C")

        monkeypatch.setattr(GitHubProvider, "get_user_info", fake_get_user_info)

        token = seed_user(client, "carol_ghlink")
        user_id = int(decode_token(token)["sub"])

        async def fake_exchange_1(self, code):
            return {"access_token": "gh-token-1"}

        monkeypatch.setattr(GitHubProvider, "exchange_code", fake_exchange_1)
        r1 = client.get(
            "/api/users/me/github-account/callback",
            params={
                "code": "x",
                "state": mint_account_link_state(user_id, return_project_id=None),
            },
            follow_redirects=False,
        )
        assert "github_account=success" in r1.headers["location"]
        first = _fetch_connection(client, user_id)
        assert decrypt_text(first.access_token) == "gh-token-1"

        async def fake_exchange_2(self, code):
            return {"access_token": "gh-token-2"}

        monkeypatch.setattr(GitHubProvider, "exchange_code", fake_exchange_2)
        r2 = client.get(
            "/api/users/me/github-account/callback",
            params={
                "code": "y",
                "state": mint_account_link_state(user_id, return_project_id=None),
            },
            follow_redirects=False,
        )
        assert "github_account=success" in r2.headers["location"]

        second = _fetch_connection(client, user_id)
        assert second.id == first.id  # updated in place, no duplicate row
        assert decrypt_text(second.access_token) == "gh-token-2"

    def test_exchange_missing_access_token_redirects_to_error(
        self, client, monkeypatch
    ):
        _enable_github_app_provider(monkeypatch)

        async def fake_exchange_code(self, code):
            return {"error": "bad_verification_code"}

        monkeypatch.setattr(GitHubProvider, "exchange_code", fake_exchange_code)

        token = seed_user(client, "dave_ghlink")
        user_id = int(decode_token(token)["sub"])
        r = client.get(
            "/api/users/me/github-account/callback",
            params={
                "code": "bad",
                "state": mint_account_link_state(user_id, return_project_id=None),
            },
            follow_redirects=False,
        )
        assert "github_account=error" in r.headers["location"]
        assert "reason=oauth_failed" in r.headers["location"]

    def test_get_github_user_token_reads_back_and_refreshes(self, client, monkeypatch):
        """OAuthService.get_github_user_token against the real repository/DB:
        live token comes back decrypted, expired+refreshable token triggers a
        refresh and persists the new one, expired+no-refresh returns None."""
        from app.domain.oauth.repositories import OAuthConnectionRepository
        from app.domain.oauth.services import OAuthService

        token = seed_user(client, "erin_ghtoken")
        user_id = int(decode_token(token)["sub"])

        async def _scenario() -> None:
            async with client.test_factory() as session:  # type: ignore[attr-defined]
                repo = OAuthConnectionRepository(session)
                svc = OAuthService(repo=repo)

                live = await repo.create(
                    user_id=user_id,
                    provider_id="github_app",
                    provider_user_id="gh-uid-erin",
                    access_token=None,
                )
                await session.commit()
                await repo.update_tokens(
                    live.id, encrypt_text("live-token"), None, None
                )
                await session.commit()
                assert await svc.get_github_user_token(user_id) == "live-token"

        async def _refresh_scenario() -> None:
            async with client.test_factory() as session:  # type: ignore[attr-defined]
                repo = OAuthConnectionRepository(session)
                svc = OAuthService(repo=repo)

                conn = await repo.get_by_user_and_provider(user_id, "github_app")
                assert conn is not None
                await repo.update_tokens(
                    conn.id,
                    encrypt_text("stale-token"),
                    encrypt_text("stored-refresh"),
                    datetime.now(UTC) - timedelta(hours=1),
                )
                await session.commit()

                svc._initialized = True

                async def fake_refresh(refresh_token: str) -> dict:
                    assert refresh_token == "stored-refresh"
                    return {"access_token": "refreshed-token", "expires_in": 28800}

                provider = GitHubProvider(
                    OAuthProviderConfig(
                        id="github_app",
                        name="GitHub (cheesex-app)",
                        client_id="cid",
                        client_secret="csec",
                        authorization_url="https://github.com/login/oauth/authorize",
                        token_url="https://github.com/login/oauth/access_token",
                        redirect_url="https://example.com/cb",
                        scope=["read:user", "user:email"],
                    )
                )
                provider.refresh_access_token = fake_refresh  # type: ignore[method-assign]
                svc._providers = {"github_app": provider}

                result = await svc.get_github_user_token(user_id)
                assert result == "refreshed-token"
                await session.commit()

                refreshed = await repo.get_by_user_and_provider(user_id, "github_app")
                assert refreshed is not None
                assert decrypt_text(refreshed.access_token) == "refreshed-token"

                # expired again, but this time with no refresh_token on file
                await repo.update_tokens(
                    refreshed.id,
                    encrypt_text("stale-again"),
                    None,
                    datetime.now(UTC) - timedelta(hours=1),
                )
                await session.commit()
                assert await svc.get_github_user_token(user_id) is None

        asyncio.run(_scenario())
        asyncio.run(_refresh_scenario())
