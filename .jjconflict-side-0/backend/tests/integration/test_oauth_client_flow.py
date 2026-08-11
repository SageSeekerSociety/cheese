"""OAuth client-completion flow (reference contract): the decision page
(stateToken), the credential-verify page (Redis pending session), account
creation and binding — everything downstream of the provider callback, which
is the part that needs no live OAuth provider."""

from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.api.routes.users import _mint_oauth_state_token
from app.core.config import settings
from tests.integration.conftest import CreatedUser, UserCreator


def _state_token(provider: str = "ruc", **info) -> str:
    payload = {
        "id": info.get("id", "prov-uid-1"),
        "email": info.get("email"),
        "name": info.get("name", "Prov User"),
        "username": info.get("username"),
        "preferredUsername": info.get("preferredUsername", "provuser"),
    }
    return _mint_oauth_state_token(provider, payload)


def _loc(resp) -> str:
    assert resp.status_code == 302, f"expected 302, got {resp.status_code}: {resp.text}"
    return resp.headers["location"]


def _q(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


class TestOAuthState:
    def test_state_decodes_and_suggests_identity(self, api_client: TestClient):
        token = _state_token(id="uid-state-1", preferredUsername="alice_prov")
        resp = api_client.get(f"/users/auth/oauth/state?token={token}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["providerId"] == "ruc"
        assert data["userInfo"]["id"] == "uid-state-1"
        assert data["suggestedUsername"].startswith("alice_prov")
        assert data["suggestedNickname"]
        assert data["emailConflict"] is False

    def test_state_reports_email_conflict(
        self, api_client: TestClient, authenticated_user: CreatedUser
    ):
        token = _state_token(email=authenticated_user.email)
        resp = api_client.get(f"/users/auth/oauth/state?token={token}")
        assert resp.json()["data"]["emailConflict"] is True

    def test_state_rejects_garbage_token(self, api_client: TestClient):
        resp = api_client.get("/users/auth/oauth/state?token=garbage")
        assert resp.status_code == 401


class TestOAuthCreate:
    def test_create_account_and_login_redirect(self, api_client: TestClient):
        token = _state_token(id="uid-create-1")
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

        # replaying the stateToken with a different username hits the
        # already-linked guard instead of minting a second account
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
        assert _q(_loc(replay))["error_code"] == "ALREADY_LINKED"

    def test_create_with_srp_password(self, api_client: TestClient):
        token = _state_token(id="uid-create-srp")
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
        self, api_client: TestClient, authenticated_user: CreatedUser
    ):
        resp = api_client.post(
            "/users/oauth/create",
            data={
                "stateToken": _state_token(id="uid-create-2"),
                "username": authenticated_user.username,
                "nickname": "x",
            },
            follow_redirects=False,
        )
        assert _q(_loc(resp))["error_code"] == "USERNAME_TAKEN"

    def test_create_rejects_bad_username_and_expired_token(
        self, api_client: TestClient
    ):
        resp = api_client.post(
            "/users/oauth/create",
            data={
                "stateToken": _state_token(id="uid-create-3"),
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
        self, api_client: TestClient, user_client: UserCreator
    ):
        user = user_client.create_user()
        resp = api_client.post(
            "/users/oauth/bind",
            data={
                "stateToken": _state_token(id=f"uid-bind-{user.user_id}"),
                "username": user.username,
                "password": user.password,
            },
            follow_redirects=False,
        )
        params = _q(_loc(resp))
        assert params["bound"] == "true"
        assert params["token"]

    def test_bind_rejects_wrong_password(
        self, api_client: TestClient, user_client: UserCreator
    ):
        user = user_client.create_user()
        resp = api_client.post(
            "/users/oauth/bind",
            data={
                "stateToken": _state_token(id="uid-bind-wrong"),
                "username": user.username,
                "password": "not-the-password",
            },
            follow_redirects=False,
        )
        assert _q(_loc(resp))["error_code"] == "INVALID_CREDENTIALS"

    def test_bind_rejects_unknown_user(self, api_client: TestClient):
        resp = api_client.post(
            "/users/oauth/bind",
            data={
                "stateToken": _state_token(id="uid-bind-nouser"),
                "username": "no_such_user_xyz",
                "password": "whatever",
            },
            follow_redirects=False,
        )
        assert _q(_loc(resp))["error_code"] == "USER_NOT_FOUND"


class TestOAuthVerifyPending:
    """The verify page redeems a Redis pending session created by the callback
    when the provider email collides with a local account."""

    def _seed_pending(self, portal, session_id: str, data: dict) -> None:
        from app.api.routes.users import _store_oauth_pending

        portal.call(_store_oauth_pending, session_id, data)

    def test_password_verify_links_and_logs_in(
        self, api_client: TestClient, user_client: UserCreator, _portal
    ):
        user = user_client.create_user()
        session_id = f"oauth_password_test_{user.user_id}"
        self._seed_pending(
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
        self._seed_pending(
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
    def test_init_rejects_non_srp_user(
        self, api_client: TestClient, user_client: UserCreator
    ):
        user = user_client.create_user()  # bcrypt legacy user
        resp = api_client.post(
            "/users/oauth/bind/srp/init",
            json={
                "stateToken": _state_token(id="uid-srpinit-1"),
                "username": user.username,
            },
        )
        assert resp.status_code == 400

    def test_init_rejects_unknown_user_and_bad_token(self, api_client: TestClient):
        resp = api_client.post(
            "/users/oauth/bind/srp/init",
            json={
                "stateToken": _state_token(id="uid-srpinit-2"),
                "username": "no_such_user_xyz",
            },
        )
        assert resp.status_code == 404

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
