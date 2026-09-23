"""/oauth/create is account registration too: when the deployment requires an
invite code, an account created through a provider needs one as well."""

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.users import _issue_oauth_state_token
from app.core.config import settings
from app.domain.invite.models import InviteCode
from app.domain.invite.services import InviteCodeService
from app.domain.user.repositories import UserRepository


def _create(api_client: TestClient, _portal, uid: str, username: str, **extra: str):
    token = _portal.call(
        _issue_oauth_state_token,
        "ruc",
        {
            "id": uid,
            "email": None,
            "name": "Prov User",
            "username": None,
            "preferredUsername": "provuser",
        },
    )
    resp = api_client.post(
        "/users/oauth/create",
        data={
            "stateToken": token,
            "username": username,
            "nickname": "invited",
            "passwordMode": "none",
            **extra,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    location = resp.headers["location"]
    return location, {k: v[0] for k, v in parse_qs(urlparse(location).query).items()}


def _account_exists(db_session: AsyncSession, _portal, username: str) -> bool:
    async def _find() -> bool:
        return await UserRepository(db_session).get_by_username(username) is not None

    return _portal.call(_find)


@pytest.fixture
def invite_required(monkeypatch):
    monkeypatch.setattr(settings, "require_invite_code", True)


@pytest.fixture
def invite_code(db_session: AsyncSession, _portal) -> str:
    async def _make() -> str:
        return (await InviteCodeService(db_session).create_code(max_uses=1)).code

    return _portal.call(_make)


def _use_count(db_session: AsyncSession, _portal, code: str) -> int:
    async def _read() -> int:
        invite = (
            await db_session.execute(select(InviteCode).where(InviteCode.code == code))
        ).scalar_one()
        await db_session.refresh(invite)
        return invite.use_count

    return _portal.call(_read)


@pytest.mark.usefixtures("invite_required")
class TestOAuthCreateRequiresInviteCode:
    def test_missing_code_is_refused(self, api_client: TestClient, db_session, _portal):
        _loc, params = _create(
            api_client, _portal, "uid-invite-none", "oauth_invite_none"
        )
        assert params["error_code"] == "INVITE_CODE_REQUIRED"
        assert not _account_exists(db_session, _portal, "oauth_invite_none")

    def test_unknown_code_is_refused(self, api_client: TestClient, db_session, _portal):
        _loc, params = _create(
            api_client, _portal, "uid-invite-bad", "oauth_invite_bad", inviteCode="nope"
        )
        assert params["error_code"] == "INVALID_INVITE_CODE"
        assert not _account_exists(db_session, _portal, "oauth_invite_bad")

    def test_valid_code_creates_account_and_is_spent(
        self, api_client: TestClient, invite_code: str, db_session, _portal
    ):
        loc, params = _create(
            api_client,
            _portal,
            "uid-invite-ok",
            "oauth_invite_ok",
            inviteCode=invite_code,
        )
        assert loc.startswith(
            f"{settings.frontend_url}{settings.frontend_oauth_success_path}"
        )
        assert params["created"] == "true"
        assert _account_exists(db_session, _portal, "oauth_invite_ok")
        assert _use_count(db_session, _portal, invite_code) == 1

        # The single use is gone: a second account cannot reuse it.
        _loc, params = _create(
            api_client,
            _portal,
            "uid-invite-again",
            "oauth_invite_again",
            inviteCode=invite_code,
        )
        assert params["error_code"] == "INVALID_INVITE_CODE"
        assert not _account_exists(db_session, _portal, "oauth_invite_again")


def test_code_is_ignored_when_not_required(
    api_client: TestClient, invite_code: str, db_session, _portal, monkeypatch
):
    monkeypatch.setattr(settings, "require_invite_code", False)
    _loc, params = _create(api_client, _portal, "uid-invite-open", "oauth_invite_open")
    assert params["created"] == "true"
    assert _use_count(db_session, _portal, invite_code) == 0
