"""Unit tests for the OAuth login/callback route handlers (redirect model).

These call the route functions directly with fake services, so they exercise
the branch logic (existing binding / email match / auto-provision / error)
without a database or HTTP layer.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.responses import RedirectResponse

from app.api.routes.users import get_oauth_login_url, handle_oauth_callback
from app.core.config import settings
from app.core.errors import NotFoundError
from app.domain.oauth.services import OAuthUserInfo


def _fake_user(user_id: int, email: str):
    return SimpleNamespace(id=user_id, email=email)


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
# callback -> resolve account and 302 to frontend success/error
# ---------------------------------------------------------------------------


def _oauth_service(*, user_info: OAuthUserInfo, existing_connection: dict | None):
    oauth = MagicMock()
    oauth.handle_callback = AsyncMock(return_value=("access-tok", user_info))
    oauth.get_connection_by_provider = AsyncMock(return_value=existing_connection)
    oauth.create_connection = AsyncMock(return_value={"id": 1})
    return oauth


def _auth_service(*, existing_user=None, created_user=None):
    auth = MagicMock()
    auth.get_user_by_email = AsyncMock(return_value=existing_user)
    auth.register_from_oauth = AsyncMock(
        return_value=(created_user, SimpleNamespace(id=1))
        if created_user
        else (None, None)
    )
    resolved = existing_user or created_user
    auth.get_user_with_profile = AsyncMock(
        return_value=(resolved, SimpleNamespace(id=1))
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
        assert "linked=true" not in loc  # existing binding is not a fresh link
        assert "REFRESH_TOKEN" in resp.headers.get("set-cookie", "")
        auth.register_from_oauth.assert_not_awaited()
        oauth.create_connection.assert_not_awaited()

    @pytest.mark.anyio
    async def test_email_match_links_existing_account(self):
        info = OAuthUserInfo(
            id="uid-2", email="b@ruc.edu.cn", name="B", preferred_username="2022"
        )
        oauth = _oauth_service(user_info=info, existing_connection=None)
        auth = _auth_service(existing_user=_fake_user(9, "b@ruc.edu.cn"))
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
        assert "linked=true" in loc
        auth.register_from_oauth.assert_not_awaited()  # matched by email, not created
        oauth.create_connection.assert_awaited_once()
        assert oauth.create_connection.await_args.kwargs["user_id"] == 9

    @pytest.mark.anyio
    async def test_new_user_is_auto_provisioned(self):
        info = OAuthUserInfo(
            id="uid-3", email="c@ruc.edu.cn", name="C", preferred_username="2023"
        )
        oauth = _oauth_service(user_info=info, existing_connection=None)
        auth = _auth_service(
            existing_user=None, created_user=_fake_user(11, "c@ruc.edu.cn")
        )
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
        assert "linked=true" in loc
        auth.register_from_oauth.assert_awaited_once()
        assert auth.register_from_oauth.await_args.kwargs["email"] == "c@ruc.edu.cn"
        oauth.create_connection.assert_awaited_once_with(
            user_id=11,
            provider_id="ruc",
            provider_user_id="uid-3",
            raw_profile={"email": "c@ruc.edu.cn", "name": "C"},
        )

    @pytest.mark.anyio
    async def test_new_user_without_email_gets_synthetic_email(self):
        info = OAuthUserInfo(
            id="uid-4", email=None, name="D", preferred_username="2024"
        )
        oauth = _oauth_service(user_info=info, existing_connection=None)
        auth = _auth_service(
            existing_user=None, created_user=_fake_user(12, "ruc-uid-4@oauth.ruc.local")
        )
        session = MagicMock(rollback=AsyncMock())

        await handle_oauth_callback(
            "ruc",
            code="c",
            state=None,
            session=session,
            oauth_service=oauth,
            auth_service=auth,
        )

        # no email from provider -> we never look up by email, and synthesize one
        auth.get_user_by_email.assert_not_awaited()
        assert (
            auth.register_from_oauth.await_args.kwargs["email"]
            == "ruc-uid-4@oauth.ruc.local"
        )

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
        info = OAuthUserInfo(
            id="uid-5", email="e@ruc.edu.cn", name="E", preferred_username="2025"
        )
        oauth = _oauth_service(user_info=info, existing_connection=None)
        auth = _auth_service(
            existing_user=None, created_user=_fake_user(13, "e@ruc.edu.cn")
        )
        auth.register_from_oauth = AsyncMock(side_effect=RuntimeError("db boom"))
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
