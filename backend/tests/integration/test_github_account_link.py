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
from app.core.github_install_state import (
    ACCOUNT_LINK_TTL_S,
    mint_account_link_state,
)
from app.core.redis import get_redis_client
from app.core.single_use_state import reserve
from app.domain.oauth.models import UserOAuthConnection
from app.domain.oauth.services import GitHubProvider, OAuthProviderConfig, OAuthUserInfo
from tests.conftest import seed_user


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# Namespace copied from the route rather than imported, so that renaming the
# route's scope shows up here as a red test instead of a silent pass.
_LINK_SCOPE = "github_account_link"


def _link_state(user_id: int, return_project_id: uuid.UUID | None = None) -> str:
    """A state the callback will actually accept — minted AND reserved.

    Minting alone stopped being enough at #222: the state is single-use now, so
    a `jti` the server never reserved takes the "already spent" exit. Without
    this helper every callback below would quietly redirect to invalid_state
    and these tests would pass while testing nothing.

    `cache_clear` because `get_redis_client` is `@lru_cache`d and its client is
    bound to whichever loop asked first — here that would be this throwaway
    `asyncio.run` loop, which the app's own request would then inherit.
    """
    minted = mint_account_link_state(user_id, return_project_id=return_project_id)
    get_redis_client.cache_clear()
    asyncio.run(reserve(_LINK_SCOPE, minted.jti, ttl_s=ACCOUNT_LINK_TTL_S))
    get_redis_client.cache_clear()
    return minted.state


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
    state = _link_state(1, return_project_id=pid)
    # No oauth provider configured → the exchange itself fails, but the
    # redirect must still land on the project that asked, not the root.
    r = client.get(
        "/api/users/me/github-account/callback",
        params={"code": "x", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert f"/projects/{pid}/settings" in r.headers["location"]
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
        state = _link_state(user_id)

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

    def test_callback_persists_a_non_ascii_profile_name(self, client, monkeypatch):
        """The first GitHub profile with a Chinese display name 500'd the
        callback (#222): json.dumps defaults to \\uXXXX escapes, and PostgreSQL
        rejects non-ASCII escapes in jsonb unless the server encoding is UTF8 —
        the dev database was initdb'd SQL_ASCII. The engine must send raw
        UTF-8. A UTF8-encoded CI database passes the round trip either way, so
        also pin the serializer wiring itself.
        """
        from app.core.db import engine

        serializer = engine.dialect._json_serializer  # type: ignore[attr-defined]
        assert "马霄宇" in serializer({"name": "马霄宇"})  # raw, not \\u9a6c…

        _enable_github_app_provider(monkeypatch)

        async def fake_exchange_code(self, code):
            return {"access_token": "gh-cn-token"}

        async def fake_get_user_info(self, access_token):
            return OAuthUserInfo(id="gh-uid-cn", email="cn@example.com", name="马霄宇")

        monkeypatch.setattr(GitHubProvider, "exchange_code", fake_exchange_code)
        monkeypatch.setattr(GitHubProvider, "get_user_info", fake_get_user_info)

        token = seed_user(client, "judy_ghcn")
        user_id = int(decode_token(token)["sub"])
        r = client.get(
            "/api/users/me/github-account/callback",
            params={
                "code": "x",
                "state": _link_state(user_id),
            },
            follow_redirects=False,
        )
        assert "github_account=success" in r.headers["location"]
        conn = _fetch_connection(client, user_id)
        assert conn.raw_profile["name"] == "马霄宇"

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
                "state": _link_state(user_id),
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
                "state": _link_state(user_id),
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
                "state": _link_state(user_id),
            },
            follow_redirects=False,
        )
        assert "github_account=error" in r.headers["location"]
        assert "reason=oauth_failed" in r.headers["location"]

    def test_already_linked_rejects_and_logs_all_three_parties(
        self, client, monkeypatch, caplog
    ):
        """One GitHub account binds to exactly one platform user — and the
        rejection must be diagnosable from the server log alone.

        From the 2026-08-10 incident (#222): a user pasted her authorize URL
        into a group chat, a teammate's browser followed it, and his
        already-bound GitHub arrived under HER state. The callback silently
        302'd; with no outcome log, reconstructing this took DB elimination.
        The log line must name the state's uid, the arriving GitHub id, and
        the id of the user who already owns it.
        """
        import logging

        _enable_github_app_provider(monkeypatch)

        async def fake_exchange_code(self, code):
            return {"access_token": "gh-token-shared"}

        async def fake_get_user_info(self, access_token):
            return OAuthUserInfo(id="gh-uid-shared", email="s@example.com", name="S")

        monkeypatch.setattr(GitHubProvider, "exchange_code", fake_exchange_code)
        monkeypatch.setattr(GitHubProvider, "get_user_info", fake_get_user_info)

        owner_token = seed_user(client, "henry_ghowner")
        owner_id = int(decode_token(owner_token)["sub"])
        victim_token = seed_user(client, "iris_ghvictim")
        victim_id = int(decode_token(victim_token)["sub"])

        route_logger = "app.api.routes.github_account_link"
        with caplog.at_level(logging.INFO, logger=route_logger):
            r1 = client.get(
                "/api/users/me/github-account/callback",
                params={
                    "code": "x",
                    "state": _link_state(owner_id),
                },
                follow_redirects=False,
            )
            assert "github_account=success" in r1.headers["location"]

            r2 = client.get(
                "/api/users/me/github-account/callback",
                params={
                    "code": "y",
                    "state": _link_state(victim_id),
                },
                follow_redirects=False,
            )

        assert r2.status_code == 302
        assert "github_account=error" in r2.headers["location"]
        assert "reason=already_linked" in r2.headers["location"]

        # The rejected user must have gained no connection row.
        async def _fetch_victim_rows() -> list[UserOAuthConnection]:
            async with client.test_factory() as session:  # type: ignore[attr-defined]
                result = await session.execute(
                    select(UserOAuthConnection).where(
                        UserOAuthConnection.user_id == victim_id,
                        UserOAuthConnection.provider_id == "github_app",
                    )
                )
                return list(result.scalars())

        assert asyncio.run(_fetch_victim_rows()) == []

        messages = [rec.getMessage() for rec in caplog.records]
        assert any(
            f"created uid={owner_id} github_id=gh-uid-shared" in m for m in messages
        ), messages
        assert any(
            f"already linked uid={victim_id} github_id=gh-uid-shared "
            f"owner_uid={owner_id}" in m
            for m in messages
        ), messages

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

                # The refresh persists in its OWN session/transaction (by
                # design — see _refresh_and_persist_token). This session's
                # identity-mapped entity still holds the pre-refresh values
                # (expire_on_commit=False), so expire before reading back.
                session.expire_all()
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

    def test_token_for_handle_round_trips_through_encryption(self, client):
        """两阶段采纳's entry point (`get_github_user_token_for_handle`) against a
        real DB: write a token through the encrypting service path, read it back
        by handle, and assert the caller gets the ORIGINAL PLAINTEXT.

        Deliberately monkeypatch-free. The only existing coverage of this
        function (tests/integration/test_accept_pr.py) stubs the whole thing
        out, which is precisely how it shipped returning raw ciphertext — a
        non-empty string that passes every `if not token` guard and then 401s
        against GitHub, indistinguishable from "no connected account".
        """
        from app.domain.oauth.repositories import OAuthConnectionRepository
        from app.domain.oauth.services import (
            OAuthService,
            get_github_user_token_for_handle,
        )

        handle = "frank_ghhandle"
        user_id = int(decode_token(seed_user(client, handle))["sub"])
        plaintext = "ghu_plaintext_secret"

        async def _scenario() -> None:
            async with client.test_factory() as session:  # type: ignore[attr-defined]
                repo = OAuthConnectionRepository(session)

                # No such user / no connected account → degrade, never raise.
                assert await get_github_user_token_for_handle(session, "nobody") is None
                assert await get_github_user_token_for_handle(session, handle) is None

                # The real write path: OAuthService encrypts on the way in.
                await OAuthService(repo=repo).create_connection(
                    user_id=user_id,
                    provider_id="github_app",
                    provider_user_id="gh-uid-frank",
                    access_token=plaintext,
                )
                await session.commit()

                stored = await repo.get_by_user_and_provider(user_id, "github_app")
                assert stored is not None
                # Prove the column really holds ciphertext, otherwise the
                # round-trip assertion below could pass vacuously.
                assert stored.access_token != plaintext
                assert decrypt_text(stored.access_token) == plaintext

                assert (
                    await get_github_user_token_for_handle(session, handle) == plaintext
                )

        asyncio.run(_scenario())

    def test_token_for_handle_degrades_to_none_instead_of_raising(self, client):
        """Every "mechanism unavailable" shape returns None so the accept flow
        falls back to the direct-merge path — none of them may raise."""
        from app.domain.oauth.repositories import OAuthConnectionRepository
        from app.domain.oauth.services import (
            OAuthService,
            get_github_user_token_for_handle,
        )

        handle = "grace_ghhandle"
        user_id = int(decode_token(seed_user(client, handle))["sub"])

        async def _scenario() -> None:
            async with client.test_factory() as session:  # type: ignore[attr-defined]
                repo = OAuthConnectionRepository(session)
                conn_dict = await OAuthService(repo=repo).create_connection(
                    user_id=user_id,
                    provider_id="github_app",
                    provider_user_id="gh-uid-grace",
                    access_token="usable-token",
                )
                await session.commit()
                conn_id = conn_dict["id"]

                # expired, nothing to refresh with
                await repo.update_tokens(
                    conn_id,
                    encrypt_text("stale-token"),
                    None,
                    datetime.now(UTC) - timedelta(hours=1),
                )
                await session.commit()
                assert await get_github_user_token_for_handle(session, handle) is None

                # ciphertext that can't be read (rotated key / legacy plaintext)
                await repo.update_tokens(conn_id, "legacy-plaintext-token", None, None)
                await session.commit()
                assert await get_github_user_token_for_handle(session, handle) is None

                # connection row exists but holds no token at all
                await repo.update_tokens(conn_id, None, None, None)
                await session.commit()
                assert await get_github_user_token_for_handle(session, handle) is None

        asyncio.run(_scenario())


class TestTheConnectionShowsAName:
    """设置页上那行「已连接 …」要给人看，不是给 GitHub 看。

    dev 上实测显示的是「已连接 222958366」——一串 GitHub 数字 id。前端本来就写了
    `login ?? providerUserId` 的降级，所以病根在后端：连接的序列化从
    `raw_profile["login"]` 取名字，而这条绑定流程存 profile 时只挑了 email 和
    name，把 GitHub 的 `login` 扔了。于是所有人、所有时候，都落到那个数字上。
    """

    def _link(self, client, monkeypatch, *, handle: str, login: str | None):
        _enable_github_app_provider(monkeypatch)

        async def fake_exchange_code(self, code):
            return {"access_token": "t-" + handle}

        async def fake_get_user_info(self, access_token):
            return OAuthUserInfo(
                id="gh-" + handle, email=None, name="N", username=login
            )

        monkeypatch.setattr(GitHubProvider, "exchange_code", fake_exchange_code)
        monkeypatch.setattr(GitHubProvider, "get_user_info", fake_get_user_info)
        token = seed_user(client, handle)
        user_id = int(decode_token(token)["sub"])
        r = client.get(
            "/api/users/me/github-account/callback",
            params={"code": "x", "state": _link_state(user_id)},
            follow_redirects=False,
        )
        assert "github_account=success" in r.headers["location"], r.headers["location"]
        return user_id, token

    def test_linking_stores_the_github_login(self, client, monkeypatch):
        user_id, _ = self._link(
            client, monkeypatch, handle="ghname_new", login="octocat"
        )

        assert _fetch_connection(client, user_id).raw_profile["login"] == "octocat"

    def test_the_connections_endpoint_hands_the_login_to_the_page(
        self, client, monkeypatch
    ):
        """前端读的是这个字段——存了但没送出去，页面照样显示数字。"""
        user_id, token = self._link(
            client, monkeypatch, handle="ghname_api", login="octocat"
        )

        # 1.0 路由自带 `/users` 前缀，网关那一层的 `/api` 在测试里不存在
        # （docs/api-conventions.md）。
        r = client.get(f"/users/{user_id}/oauth/connections", headers=_bearer(token))

        assert r.status_code == 200, r.text
        rows = r.json()["data"]["connections"]
        gh = next(c for c in rows if c["providerId"] == "github_app")
        assert gh["login"] == "octocat"

    def test_relinking_repairs_a_connection_that_has_no_login(
        self, client, monkeypatch
    ):
        """这条是整个修复能不能落到真实用户身上的那一条。

        所有人都已经绑过了，所以新建路径修好对他们没有任何影响——他们走的是
        「重新连接」，而那条分支以前只换 token、根本不碰 profile。不修这半，线上
        每个人的设置页会一直显示那串数字，而且没有任何自助的修法（login 只能从
        GitHub 拿，写不出补数据的迁移）。
        """
        user_id, _ = self._link(client, monkeypatch, handle="ghname_old", login=None)
        assert _fetch_connection(client, user_id).raw_profile.get("login") is None

        # 用户点「重新连接」，这次 GitHub 把 login 给了。
        self._relink(
            client, monkeypatch, user_id=user_id, handle="ghname_old", login="octocat"
        )

        assert _fetch_connection(client, user_id).raw_profile["login"] == "octocat"

    def test_relinking_without_a_login_keeps_the_one_we_had(self, client, monkeypatch):
        """重新连接不能把已经好了的名字降级回去。

        这条是本 PR 自己的反向风险：既然重新连接现在会覆盖 profile，那么某次
        交换没带回 login 时照写，就会把 octocat 抹成 None、页面退回数字 id——
        而且恰好发生在用户点「重新连接」想修好它的时候。
        """
        user_id, _ = self._link(
            client, monkeypatch, handle="ghname_keep", login="octocat"
        )

        self._relink(
            client, monkeypatch, user_id=user_id, handle="ghname_keep", login=None
        )

        assert _fetch_connection(client, user_id).raw_profile["login"] == "octocat"

    def _relink(
        self, client, monkeypatch, *, user_id: int, handle: str, login: str | None
    ):
        async def fake_get_user_info(self, access_token):
            return OAuthUserInfo(
                id="gh-" + handle, email=None, name="N", username=login
            )

        monkeypatch.setattr(GitHubProvider, "get_user_info", fake_get_user_info)
        r = client.get(
            "/api/users/me/github-account/callback",
            params={"code": "x", "state": _link_state(user_id)},
            follow_redirects=False,
        )
        assert "github_account=success" in r.headers["location"], r.headers["location"]
