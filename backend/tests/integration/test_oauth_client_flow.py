"""OAuth client-completion flow (reference contract): the decision page
(stateToken), the credential-verify page (Redis pending session), account
creation and binding — everything downstream of the provider callback, which
is the part that needs no live OAuth provider."""

from collections.abc import Callable
from urllib.parse import parse_qs, urlparse

import pyotp
import pytest
from fastapi.testclient import TestClient

from app.api.routes.users import _issue_oauth_state_token
from app.core.config import settings
from tests.integration.conftest import CreatedUser, UserCreator
from tests.support.consent import OAUTH_CONSENT_FORM

StateToken = Callable[..., str]


@pytest.fixture
def state_token(_portal) -> StateToken:
    """A stateToken issued the way the callback issues one — minted AND
    reserved, so the endpoints that spend it can actually claim it."""

    def issue(provider: str = "ruc", **info) -> str:
        payload = {
            "id": info.get("id", "prov-uid-1"),
            "email": info.get("email"),
            "name": info.get("name", "Prov User"),
            "username": info.get("username"),
            "preferredUsername": info.get("preferredUsername", "provuser"),
        }
        return _portal.call(_issue_oauth_state_token, provider, payload)

    return issue


def _loc(resp) -> str:
    assert resp.status_code == 302, f"expected 302, got {resp.status_code}: {resp.text}"
    return resp.headers["location"]


def _q(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def _bind(client: TestClient, token: str, username: str, password: str):
    return client.post(
        "/users/oauth/bind",
        data={"stateToken": token, "username": username, "password": password},
        follow_redirects=False,
    )


def _login(client: TestClient, user: CreatedUser, password: str | None = None):
    return client.post(
        "/users/auth/login",
        json={"username": user.username, "password": password or user.password},
    )


def _enable_2fa(client: TestClient, user: CreatedUser) -> str:
    resp = _login(client, user)
    assert resp.status_code == 200, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}
    sudo = client.post(
        "/users/auth/sudo",
        headers=headers,
        json={
            "method": "password",
            "credentials": {"password": user.password},
            "purpose": "2fa:enable",
        },
    )
    assert sudo.status_code == 200, sudo.text
    init = client.post(
        f"/users/{user.user_id}/2fa/enable",
        headers=headers,
        json={"sudoTicket": sudo.json()["data"]["sudoTicket"]},
    )
    secret = init.json()["data"]["secret"]
    confirm = client.post(
        f"/users/{user.user_id}/2fa/enable",
        headers=headers,
        json={"secret": secret, "code": pyotp.TOTP(secret).now()},
    )
    assert confirm.status_code == 200, confirm.text
    return secret


def _seed_pending(portal, session_id: str, data: dict) -> None:
    from app.api.routes.users import _store_oauth_pending

    portal.call(_store_oauth_pending, session_id, data)


class TestOAuthState:
    def test_state_decodes_and_suggests_identity(
        self, api_client: TestClient, state_token: StateToken
    ):
        token = state_token(id="uid-state-1", preferredUsername="alice_prov")
        resp = api_client.get(f"/users/auth/oauth/state?token={token}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["providerId"] == "ruc"
        assert data["userInfo"]["id"] == "uid-state-1"
        assert data["suggestedUsername"].startswith("alice_prov")
        assert data["suggestedNickname"]
        assert data["emailConflict"] is False

    def test_state_reports_email_conflict(
        self,
        api_client: TestClient,
        authenticated_user: CreatedUser,
        state_token: StateToken,
    ):
        token = state_token(email=authenticated_user.email)
        resp = api_client.get(f"/users/auth/oauth/state?token={token}")
        assert resp.json()["data"]["emailConflict"] is True

    def test_state_rejects_garbage_token(self, api_client: TestClient):
        resp = api_client.get("/users/auth/oauth/state?token=garbage")
        assert resp.status_code == 401

    def test_reading_the_state_does_not_spend_it(
        self, api_client: TestClient, user_client: UserCreator, state_token: StateToken
    ):
        # The decision page reads the token on load and spends it on submit.
        user = user_client.create_user()
        token = state_token(id=f"uid-state-read-{user.user_id}")
        assert api_client.get(f"/users/auth/oauth/state?token={token}").is_success

        resp = _bind(api_client, token, user.username, user.password)
        assert _q(_loc(resp))["bound"] == "true"


class TestOAuthCreate:
    def test_create_account_and_login_redirect(
        self, api_client: TestClient, state_token: StateToken
    ):
        token = state_token(id="uid-create-1")
        resp = api_client.post(
            "/users/oauth/create",
            data={
                **OAUTH_CONSENT_FORM,
                "stateToken": token,
                "username": "oauth_created_1",
                "nickname": "created",
                "passwordMode": "none",
            },
            follow_redirects=False,
        )
        loc = _loc(resp)
        assert loc.startswith(
            f"{settings.frontend_url}{settings.frontend_oauth_success_path}"
        )
        params = _q(loc)
        assert params["created"] == "true"
        assert params["authMode"] == "none"
        assert params["token"]
        assert "REFRESH_TOKEN" in resp.headers.get("set-cookie", "")

        # the minted token is a live session for the new account
        me = api_client.get(
            "/users/auth/methods/oauth_created_1",
        )
        assert me.status_code == 200

        # the stateToken is spent: a replay cannot mint a second account
        replay = api_client.post(
            "/users/oauth/create",
            data={
                **OAUTH_CONSENT_FORM,
                "stateToken": token,
                "username": "oauth_created_replay",
                "nickname": "again",
                "passwordMode": "none",
            },
            follow_redirects=False,
        )
        assert _q(_loc(replay))["error_code"] == "TOKEN_EXPIRED"

    def test_create_with_plaintext_password(
        self, api_client: TestClient, state_token: StateToken
    ):
        resp = api_client.post(
            "/users/oauth/create",
            data={
                **OAUTH_CONSENT_FORM,
                "stateToken": state_token(id="uid-create-plain"),
                "username": "oauth_created_plain",
                "nickname": "plain_user",
                "passwordMode": "password",
                "password": "oauth-pass~1",
            },
            follow_redirects=False,
        )
        assert _q(_loc(resp))["created"] == "true"

        login = api_client.post(
            "/users/auth/login",
            json={"username": "oauth_created_plain", "password": "oauth-pass~1"},
        )
        assert login.status_code == 200, login.text
        assert login.json()["data"]["accessToken"]

    @pytest.mark.parametrize(
        "password",
        ["", "letters0nly", "no-digits!", "x!1" * 30],
        ids=["missing", "no-symbol", "no-digit", "over-72-bytes"],
    )
    def test_create_refuses_a_password_before_spending_the_state_token(
        self, api_client: TestClient, state_token: StateToken, password: str
    ):
        token = state_token(id="uid-create-weak")
        form = {
            "stateToken": token,
            "username": "oauth_created_weak",
            "nickname": "weak_user",
            "passwordMode": "password",
            "password": password,
            **OAUTH_CONSENT_FORM,
        }
        refused = api_client.post(
            "/users/oauth/create", data=form, follow_redirects=False
        )
        assert _q(_loc(refused))["error_code"] == "WEAK_PASSWORD"

        # Neither the token nor the username was spent on the refusal.
        retried = api_client.post(
            "/users/oauth/create",
            data={**form, "password": "a-valid-password-1"},
            follow_redirects=False,
        )
        assert _q(_loc(retried))["created"] == "true"

    def test_create_rejects_taken_username(
        self,
        api_client: TestClient,
        authenticated_user: CreatedUser,
        state_token: StateToken,
    ):
        resp = api_client.post(
            "/users/oauth/create",
            data={
                **OAUTH_CONSENT_FORM,
                "stateToken": state_token(id="uid-create-2"),
                "username": authenticated_user.username,
                "nickname": "x",
            },
            follow_redirects=False,
        )
        assert _q(_loc(resp))["error_code"] == "USERNAME_TAKEN"

    def test_create_rejects_bad_username_and_expired_token(
        self, api_client: TestClient, state_token: StateToken
    ):
        resp = api_client.post(
            "/users/oauth/create",
            data={
                **OAUTH_CONSENT_FORM,
                "stateToken": state_token(id="uid-create-3"),
                "username": "ab",  # too short
                "nickname": "x",
            },
            follow_redirects=False,
        )
        assert _q(_loc(resp))["error_code"] == "INVALID_USERNAME"

        resp = api_client.post(
            "/users/oauth/create",
            data={"stateToken": "garbage", "username": "validname", "nickname": "x"},
            follow_redirects=False,
        )
        assert _q(_loc(resp))["error_code"] == "TOKEN_EXPIRED"


class TestOAuthBindPassword:
    def test_bind_legacy_password_account(
        self, api_client: TestClient, user_client: UserCreator, state_token: StateToken
    ):
        user = user_client.create_user()
        resp = _bind(
            api_client,
            state_token(id=f"uid-bind-{user.user_id}"),
            user.username,
            user.password,
        )
        params = _q(_loc(resp))
        assert params["bound"] == "true"
        assert params["token"]

    def test_bind_rejects_wrong_password(
        self, api_client: TestClient, user_client: UserCreator, state_token: StateToken
    ):
        user = user_client.create_user()
        resp = _bind(
            api_client, state_token(id="uid-bind-wrong"), user.username, "not-it"
        )
        assert _q(_loc(resp))["error_code"] == "INVALID_CREDENTIALS"

    def test_unknown_user_is_answered_like_a_wrong_password(
        self, api_client: TestClient, state_token: StateToken
    ):
        resp = _bind(
            api_client, state_token(id="uid-bind-nouser"), "no_such_user_xyz", "x"
        )
        assert _q(_loc(resp))["error_code"] == "INVALID_CREDENTIALS"

    def test_state_token_binds_only_once(
        self, api_client: TestClient, user_client: UserCreator, state_token: StateToken
    ):
        first, second = user_client.create_user(), user_client.create_user()
        token = state_token(id="uid-bind-once")
        assert (
            _q(_loc(_bind(api_client, token, first.username, first.password)))["bound"]
            == "true"
        )

        replay = _bind(api_client, token, second.username, second.password)
        assert _q(_loc(replay))["error_code"] == "TOKEN_EXPIRED"

    def test_a_failed_attempt_spends_the_state_token(
        self, api_client: TestClient, user_client: UserCreator, state_token: StateToken
    ):
        user = user_client.create_user()
        token = state_token(id="uid-bind-spent")
        _bind(api_client, token, user.username, "wrong")

        retry = _bind(api_client, token, user.username, user.password)
        assert _q(_loc(retry))["error_code"] == "TOKEN_EXPIRED"


class TestBindingSharesTheLoginBudget:
    def test_login_lockout_blocks_binding(
        self, api_client: TestClient, user_client: UserCreator, state_token: StateToken
    ):
        user = user_client.create_user()
        for _ in range(5):
            _login(api_client, user, "wrong-password")

        resp = _bind(
            api_client, state_token(id="uid-locked-1"), user.username, user.password
        )
        assert _q(_loc(resp))["error_code"] == "TOO_MANY_ATTEMPTS"

    def test_failed_bindings_lock_the_login(
        self, api_client: TestClient, user_client: UserCreator, state_token: StateToken
    ):
        user = user_client.create_user()
        for i in range(5):
            _bind(
                api_client, state_token(id=f"uid-locked-2-{i}"), user.username, "wrong"
            )

        assert _login(api_client, user).status_code == 403
        resp = _bind(
            api_client, state_token(id="uid-locked-2-x"), user.username, user.password
        )
        assert _q(_loc(resp))["error_code"] == "TOO_MANY_ATTEMPTS"

    def test_verify_page_draws_on_the_same_budget(
        self, api_client: TestClient, user_client: UserCreator, _portal
    ):
        user = user_client.create_user()
        for _ in range(5):
            _login(api_client, user, "wrong-password")

        session_id = f"oauth_password_locked_{user.user_id}"
        _seed_pending(
            _portal,
            session_id,
            {
                "type": "password",
                "providerId": "ruc",
                "userInfo": {"id": f"uid-verify-locked-{user.user_id}"},
                "userId": user.user_id,
                "username": user.username,
            },
        )
        resp = api_client.post(
            "/users/auth/oauth/verify",
            json={"sessionId": session_id, "password": user.password},
            follow_redirects=False,
        )
        assert _q(_loc(resp))["error_code"] == "TOO_MANY_ATTEMPTS"


class TestOAuthRespectsTwoFactor:
    """Signing in through a provider replaces the password step only."""

    def _assert_2fa_ticket(self, client: TestClient, resp, secret: str) -> None:
        loc = _loc(resp)
        assert loc.startswith(f"{settings.frontend_url}/account/verify-2fa?")
        assert "REFRESH_TOKEN" not in resp.headers.get("set-cookie", "")
        done = client.post(
            "/users/auth/verify-2fa",
            json={"temp_token": _q(loc)["token"], "code": pyotp.TOTP(secret).now()},
        )
        assert done.status_code == 200, done.text
        assert done.json()["data"]["accessToken"]

    def test_binding_a_2fa_account_asks_for_the_second_factor(
        self, api_client: TestClient, user_client: UserCreator, state_token: StateToken
    ):
        user = user_client.create_user()
        secret = _enable_2fa(api_client, user)

        resp = _bind(
            api_client,
            state_token(id=f"uid-2fa-bind-{user.user_id}"),
            user.username,
            user.password,
        )
        self._assert_2fa_ticket(api_client, resp, secret)

    def test_verify_page_asks_a_2fa_account_for_the_second_factor(
        self, api_client: TestClient, user_client: UserCreator, _portal
    ):
        user = user_client.create_user()
        secret = _enable_2fa(api_client, user)
        session_id = f"oauth_password_2fa_{user.user_id}"
        _seed_pending(
            _portal,
            session_id,
            {
                "type": "password",
                "providerId": "ruc",
                "userInfo": {"id": f"uid-2fa-verify-{user.user_id}"},
                "userId": user.user_id,
                "username": user.username,
            },
        )
        resp = api_client.post(
            "/users/auth/oauth/verify",
            json={"sessionId": session_id, "password": user.password},
            follow_redirects=False,
        )
        self._assert_2fa_ticket(api_client, resp, secret)

    def test_signing_in_with_a_linked_provider_asks_for_the_second_factor(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        state_token: StateToken,
        monkeypatch,
    ):
        from app.domain.oauth.services import GitHubProvider, OAuthUserInfo

        monkeypatch.setattr(settings, "oauth_enabled_providers", "github")
        monkeypatch.setattr(settings, "oauth_github_client_id", "test-client-id")
        monkeypatch.setattr(settings, "oauth_github_client_secret", "test-secret")
        monkeypatch.setattr(
            settings,
            "oauth_github_redirect_url",
            "http://testserver/users/auth/oauth/callback/github",
        )

        async def fake_exchange_code(self, code):
            return {"access_token": "gh-token"}

        async def fake_get_user_info(self, access_token):
            return OAuthUserInfo(id="gh-2fa-uid", email=None, name="G")

        monkeypatch.setattr(GitHubProvider, "exchange_code", fake_exchange_code)
        monkeypatch.setattr(GitHubProvider, "get_user_info", fake_get_user_info)

        user = user_client.create_user()
        linked = _bind(
            api_client,
            state_token("github", id="gh-2fa-uid"),
            user.username,
            user.password,
        )
        assert _q(_loc(linked))["bound"] == "true"
        secret = _enable_2fa(api_client, user)

        start = api_client.get("/users/auth/oauth/login/github", follow_redirects=False)
        state = _q(start.headers["location"])["state"]
        resp = api_client.get(
            "/users/auth/oauth/callback/github",
            params={"code": "c", "state": state},
            follow_redirects=False,
        )
        self._assert_2fa_ticket(api_client, resp, secret)


class TestOAuthVerifyPending:
    """The verify page redeems a Redis pending session created by the callback
    when the provider email collides with a local account."""

    def test_password_verify_links_and_logs_in(
        self, api_client: TestClient, user_client: UserCreator, _portal
    ):
        user = user_client.create_user()
        session_id = f"oauth_password_test_{user.user_id}"
        _seed_pending(
            _portal,
            session_id,
            {
                "type": "password",
                "providerId": "ruc",
                "userInfo": {"id": f"uid-verify-{user.user_id}", "email": user.email},
                "userId": user.user_id,
                "username": user.username,
            },
        )

        resp = api_client.post(
            "/users/auth/oauth/verify",
            json={"sessionId": session_id, "password": user.password},
            follow_redirects=False,
        )
        params = _q(_loc(resp))
        assert params["linked"] == "true"
        assert params["token"]

        # one-shot: the pending session is consumed
        replay = api_client.post(
            "/users/auth/oauth/verify",
            json={"sessionId": session_id, "password": user.password},
            follow_redirects=False,
        )
        assert _q(_loc(replay))["error_code"] == "SESSION_EXPIRED"

    def test_wrong_password_is_rejected(
        self, api_client: TestClient, user_client: UserCreator, _portal
    ):
        user = user_client.create_user()
        session_id = f"oauth_password_bad_{user.user_id}"
        _seed_pending(
            _portal,
            session_id,
            {
                "type": "password",
                "providerId": "ruc",
                "userInfo": {"id": f"uid-verify-bad-{user.user_id}"},
                "userId": user.user_id,
                "username": user.username,
            },
        )
        resp = api_client.post(
            "/users/auth/oauth/verify",
            json={"sessionId": session_id, "password": "wrong"},
            follow_redirects=False,
        )
        assert _q(_loc(resp))["error_code"] == "INVALID_PASSWORD"

    def test_unknown_session_expires(self, api_client: TestClient):
        resp = api_client.post(
            "/users/auth/oauth/verify",
            json={"sessionId": "nope", "password": "x"},
            follow_redirects=False,
        )
        assert _q(_loc(resp))["error_code"] == "SESSION_EXPIRED"
