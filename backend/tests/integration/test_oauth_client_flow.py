"""OAuth client-completion flow (reference contract): the decision page
(stateToken), the credential-verify page (Redis pending session), account
creation and binding — everything downstream of the provider callback, which
is the part that needs no live OAuth provider."""

from collections.abc import Callable
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.api.routes.users import _issue_oauth_state_token
from app.core.config import settings
from tests.integration.conftest import CreatedUser, UserCreator

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
                "stateToken": token,
                "username": "oauth_created_replay",
                "nickname": "again",
                "passwordMode": "none",
            },
            follow_redirects=False,
        )
        assert _q(_loc(replay))["error_code"] == "TOKEN_EXPIRED"

    def test_create_with_srp_password(
        self, api_client: TestClient, state_token: StateToken
    ):
        token = state_token(id="uid-create-srp")
        resp = api_client.post(
            "/users/oauth/create",
            data={
                "stateToken": token,
                "username": "oauth_created_srp",
                "nickname": "srp_user",
                "passwordMode": "srp",
                "srpSalt": "aa" * 8,
                "srpVerifier": "bb" * 8,
            },
            follow_redirects=False,
        )
        assert _q(_loc(resp))["authMode"] == "srp"

        methods = api_client.get("/users/auth/methods/oauth_created_srp")
        assert methods.status_code == 200
        assert methods.json()["data"]["supports_srp"] is True

    def test_create_rejects_taken_username(
        self,
        api_client: TestClient,
        authenticated_user: CreatedUser,
        state_token: StateToken,
    ):
        resp = api_client.post(
            "/users/oauth/create",
            data={
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


class TestOAuthSrpBind:
    def _srp_user(self, client: TestClient, state_token: StateToken) -> str:
        username = "oauth_srp_binder"
        resp = client.post(
            "/users/oauth/create",
            data={
                "stateToken": state_token(id="uid-srp-owner"),
                "username": username,
                "nickname": "srp",
                "passwordMode": "srp",
                "srpSalt": "aa" * 8,
                "srpVerifier": "bb" * 8,
            },
            follow_redirects=False,
        )
        assert _q(_loc(resp))["created"] == "true"
        return username

    def test_init_spends_the_state_token(
        self, api_client: TestClient, state_token: StateToken
    ):
        username = self._srp_user(api_client, state_token)
        token = state_token(id="uid-srpinit-once")
        body = {"stateToken": token, "username": username}

        first = api_client.post("/users/oauth/bind/srp/init", json=body)
        assert first.status_code == 200, first.text
        assert first.json()["data"]["sessionId"]

        assert (
            api_client.post("/users/oauth/bind/srp/init", json=body).status_code == 401
        )

    def test_init_answers_unknown_and_non_srp_users_alike(
        self, api_client: TestClient, user_client: UserCreator, state_token: StateToken
    ):
        legacy = user_client.create_user()  # bcrypt legacy user
        not_srp = api_client.post(
            "/users/oauth/bind/srp/init",
            json={
                "stateToken": state_token(id="uid-srpinit-1"),
                "username": legacy.username,
            },
        )
        unknown = api_client.post(
            "/users/oauth/bind/srp/init",
            json={
                "stateToken": state_token(id="uid-srpinit-2"),
                "username": "no_such_user_xyz",
            },
        )
        assert not_srp.status_code == unknown.status_code == 401
        assert not_srp.json()["message"] == unknown.json()["message"]

    def test_init_rejects_bad_token(self, api_client: TestClient):
        resp = api_client.post(
            "/users/oauth/bind/srp/init",
            json={"stateToken": "garbage", "username": "whoever"},
        )
        assert resp.status_code == 401

    def test_verify_with_unknown_session_redirects_expired(
        self, api_client: TestClient
    ):
        resp = api_client.post(
            "/users/oauth/bind/srp/verify",
            data={
                "sessionId": "nope",
                "clientPublicEphemeral": "aa",
                "clientProof": "bb",
            },
            follow_redirects=False,
        )
        assert _q(_loc(resp))["error_code"] == "SESSION_EXPIRED"
