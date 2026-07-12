"""Unit tests for the OAuth login/callback route handlers (redirect model).

These call the route functions directly with fake services, so they exercise
the branch logic (existing binding / email conflict → verify page / unknown
identity → decision page / error) without a database or HTTP layer.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.responses import RedirectResponse

from app.api.routes.users import (
    _decode_oauth_state_token,
    get_oauth_login_url,
    handle_oauth_callback,
)
from app.core.config import settings
from app.core.errors import NotFoundError
from app.domain.oauth.services import OAuthUserInfo


def _fake_user(user_id: int, email: str, *, hashed_password: str | None = "$2b$fake"):
    return SimpleNamespace(
        id=user_id,
        email=email,
        username=f"user{user_id}",
        hashed_password=hashed_password,
    )


def _location(resp: RedirectResponse) -> str:
    return resp.headers["location"]


# ---------------------------------------------------------------------------
# login -> 302 to provider authorization URL
# ---------------------------------------------------------------------------


class TestOAuthLoginRedirect:
    @pytest.mark.anyio
    async def test_redirects_to_authorization_url(self):
        oauth = MagicMock()
        oauth.generate_authorization_url.return_value = (
            "https://v.ruc.edu.cn/oauth2/authorize?x=1"
        )

        resp = await get_oauth_login_url("ruc", state="st-1", oauth_service=oauth)

        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 302
        assert _location(resp) == "https://v.ruc.edu.cn/oauth2/authorize?x=1"
        oauth.generate_authorization_url.assert_called_once_with("ruc", "st-1")

    @pytest.mark.anyio
    async def test_unknown_provider_raises_not_found(self):
        oauth = MagicMock()
        oauth.generate_authorization_url.side_effect = NotFoundError("nope")

        with pytest.raises(NotFoundError):
            await get_oauth_login_url("nope", state=None, oauth_service=oauth)


# ---------------------------------------------------------------------------
# callback -> existing binding logs in; conflicts and unknowns hand off to
# the frontend verify/decision pages
# ---------------------------------------------------------------------------


def _oauth_service(*, user_info: OAuthUserInfo, existing_connection: dict | None):
    oauth = MagicMock()
    oauth.handle_callback = AsyncMock(return_value=("access-tok", user_info))
    oauth.get_connection_by_provider = AsyncMock(return_value=existing_connection)
    oauth.create_connection = AsyncMock(return_value={"id": 1})
    return oauth


def _auth_service(*, existing_user=None):
    auth = MagicMock()
    auth.get_user_by_email = AsyncMock(return_value=existing_user)
    auth.get_user_with_profile = AsyncMock(
        return_value=(existing_user, SimpleNamespace(id=1))
    )
    return auth


class TestOAuthCallback:
    @pytest.mark.anyio
    async def test_existing_binding_logs_in(self):
        info = OAuthUserInfo(
            id="uid-1", email="a@ruc.edu.cn", name="A", preferred_username="2021"
        )
        oauth = _oauth_service(user_info=info, existing_connection={"userId": 7})
        auth = _auth_service(existing_user=_fake_user(7, "a@ruc.edu.cn"))
        session = MagicMock(rollback=AsyncMock())

        resp = await handle_oauth_callback(
            "ruc",
            code="c",
            state="s",
            session=session,
            oauth_service=oauth,
            auth_service=auth,
        )

        loc = _location(resp)
        assert resp.status_code == 302
        assert loc.startswith(
            f"{settings.frontend_url}{settings.frontend_oauth_success_path}"
        )
        assert "token=" in loc and "provider=ruc" in loc
        assert "REFRESH_TOKEN" in resp.headers.get("set-cookie", "")
        oauth.create_connection.assert_not_awaited()

    @pytest.mark.anyio
    async def test_email_conflict_redirects_to_verify_page(self, monkeypatch):
        info = OAuthUserInfo(
            id="uid-2", email="b@ruc.edu.cn", name="B", preferred_username="2022"
        )
        oauth = _oauth_service(user_info=info, existing_connection=None)
        auth = _auth_service(existing_user=_fake_user(9, "b@ruc.edu.cn"))
        session = MagicMock(rollback=AsyncMock())
        stored: dict = {}

        async def _fake_store(session_id, data):
            stored[session_id] = data

        monkeypatch.setattr("app.api.routes.users._store_oauth_pending", _fake_store)

        resp = await handle_oauth_callback(
            "ruc",
            code="c",
            state=None,
            session=session,
            oauth_service=oauth,
            auth_service=auth,
        )

        loc = _location(resp)
        assert loc.startswith(
            f"{settings.frontend_url}{settings.frontend_oauth_verify_path}"
        )
        assert "type=password" in loc and "sessionId=" in loc
        # NO silent link, NO login: ownership must be proven first
        oauth.create_connection.assert_not_awaited()
        assert "token=" not in loc
        (pending,) = stored.values()
        assert pending["userId"] == 9 and pending["type"] == "password"

    @pytest.mark.anyio
    async def test_email_conflict_srp_user_gets_srp_params(self, monkeypatch):
        info = OAuthUserInfo(id="uid-6", email="s@ruc.edu.cn", name="S")
        oauth = _oauth_service(user_info=info, existing_connection=None)
        auth = _auth_service(
            existing_user=_fake_user(
                21, "s@ruc.edu.cn", hashed_password="SRP:aa11:bb22"
            )
        )
        session = MagicMock(rollback=AsyncMock())

        monkeypatch.setattr("app.api.routes.users._store_oauth_pending", AsyncMock())
        monkeypatch.setattr(
            "app.api.routes.users._srp_generate_ephemeral",
            lambda verifier: ("srv-pub", "srv-sec"),
        )

        resp = await handle_oauth_callback(
            "ruc",
            code="c",
            state=None,
            session=session,
            oauth_service=oauth,
            auth_service=auth,
        )

        loc = _location(resp)
        assert "type=srp" in loc
        assert "salt=aa11" in loc
        assert "serverPublicEphemeral=srv-pub" in loc

    @pytest.mark.anyio
    async def test_unknown_identity_redirects_to_decision_page(self):
        info = OAuthUserInfo(
            id="uid-3", email="c@ruc.edu.cn", name="C", preferred_username="2023"
        )
        oauth = _oauth_service(user_info=info, existing_connection=None)
        auth = _auth_service(existing_user=None)
        session = MagicMock(rollback=AsyncMock())

        resp = await handle_oauth_callback(
            "ruc",
            code="c",
            state=None,
            session=session,
            oauth_service=oauth,
            auth_service=auth,
        )

        loc = _location(resp)
        assert loc.startswith(
            f"{settings.frontend_url}{settings.frontend_oauth_complete_path}"
        )
        assert "stateToken=" in loc
        oauth.create_connection.assert_not_awaited()

        # the stateToken round-trips the provider identity
        from urllib.parse import parse_qs, urlparse

        token = parse_qs(urlparse(loc).query)["stateToken"][0]
        provider_id, user_info = _decode_oauth_state_token(token)
        assert provider_id == "ruc"
        assert user_info["id"] == "uid-3"
        assert user_info["email"] == "c@ruc.edu.cn"

    @pytest.mark.anyio
    async def test_no_email_skips_lookup_and_goes_to_decision_page(self):
        info = OAuthUserInfo(id="uid-4", email=None, name="D", preferred_username="x")
        oauth = _oauth_service(user_info=info, existing_connection=None)
        auth = _auth_service(existing_user=None)
        session = MagicMock(rollback=AsyncMock())

        resp = await handle_oauth_callback(
            "ruc",
            code="c",
            state=None,
            session=session,
            oauth_service=oauth,
            auth_service=auth,
        )

        auth.get_user_by_email.assert_not_awaited()
        assert "stateToken=" in _location(resp)

    @pytest.mark.anyio
    async def test_exchange_failure_redirects_to_error(self):
        oauth = MagicMock()
        oauth.handle_callback = AsyncMock(
            side_effect=RuntimeError("token exchange failed")
        )
        auth = _auth_service()
        session = MagicMock(rollback=AsyncMock())

        resp = await handle_oauth_callback(
            "ruc",
            code="bad",
            state=None,
            session=session,
            oauth_service=oauth,
            auth_service=auth,
        )

        loc = _location(resp)
        assert resp.status_code == 302
        assert loc.startswith(
            f"{settings.frontend_url}{settings.frontend_oauth_error_path}"
        )
        assert "message=oauth_failed" in loc

    @pytest.mark.anyio
    async def test_account_error_rolls_back_and_redirects_to_error(self):
        info = OAuthUserInfo(id="uid-5", email="e@ruc.edu.cn", name="E")
        oauth = _oauth_service(user_info=info, existing_connection=None)
        oauth.get_connection_by_provider = AsyncMock(
            side_effect=RuntimeError("db boom")
        )
        auth = _auth_service(existing_user=None)
        session = MagicMock(rollback=AsyncMock())

        resp = await handle_oauth_callback(
            "ruc",
            code="c",
            state=None,
            session=session,
            oauth_service=oauth,
            auth_service=auth,
        )

        loc = _location(resp)
        assert loc.startswith(
            f"{settings.frontend_url}{settings.frontend_oauth_error_path}"
        )
        assert "message=account_error" in loc
        session.rollback.assert_awaited_once()
