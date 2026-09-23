"""Changing the password from inside a signed-in session (#1479).

The new password arrives as SRP credentials the client derived itself, so the
proof that it took is signing in with it — and that the old one no longer
does.
"""

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import CreatedUser, UserCreator
from tests.unit.test_srp import _client_prove, _client_register


class TestChangePassword:
    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        yield
        # Attempt budgets live in Redis and outlive the per-test transaction,
        # while user ids restart with each session's fresh database.
        import redis

        from app.core.config import settings
        from app.domain.user.login_security import (
            LOGIN_ATTEMPTS_PREFIX,
            LOGIN_LOCKOUT_PREFIX,
            STEP_UP_PASSWORD_ATTEMPTS_PREFIX,
            STEP_UP_PASSWORD_LOCKOUT_PREFIX,
        )

        r = redis.Redis.from_url(settings.redis_url)
        r.delete(
            f"{STEP_UP_PASSWORD_ATTEMPTS_PREFIX}{self.user.user_id}",
            f"{STEP_UP_PASSWORD_LOCKOUT_PREFIX}{self.user.user_id}",
            f"{LOGIN_ATTEMPTS_PREFIX}{self.user.username}",
            f"{LOGIN_LOCKOUT_PREFIX}{self.user.username}",
        )
        r.close()

    def _ticket(self, purpose: str = "password:change") -> str:
        resp = self.client.post(
            "/users/auth/sudo",
            headers=self.headers,
            json={
                "method": "password",
                "credentials": {"password": self.user.password},
                "purpose": purpose,
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]["sudoTicket"]

    def _change(
        self,
        new_password: str,
        ticket: str | None,
        *,
        headers: dict[str, str] | None = None,
    ):
        salt, verifier = _client_register(self.user.username, new_password)
        body = {"srpSalt": salt, "srpVerifier": verifier}
        if ticket is not None:
            body["sudoTicket"] = ticket
        return self.client.patch(
            f"/users/{self.user.user_id}/password",
            headers=headers or self.headers,
            json=body,
        )

    def _srp_login(self, password: str):
        init = self.client.post(
            "/users/auth/srp/init", json={"username": self.user.username}
        )
        if init.status_code != 200:
            return init
        data = init.json()["data"]
        client_public, client_proof, _key = _client_prove(
            self.user.username, password, data["salt"], data["serverPublicEphemeral"]
        )
        return self.client.post(
            "/users/auth/srp/verify",
            json={
                "username": self.user.username,
                "clientPublicEphemeral": client_public,
                "clientProof": client_proof,
            },
        )

    def _password_login(self, password: str):
        return self.client.post(
            "/users/auth/login",
            json={"username": self.user.username, "password": password},
        )

    def test_the_new_password_signs_in_and_the_old_one_does_not(self):
        old = self.user.password
        new = "a-brand-new-password"

        resp = self._change(new, self._ticket())
        assert resp.status_code == 200, resp.text

        signed_in = self._srp_login(new)
        assert signed_in.status_code == 200, signed_in.text
        assert signed_in.json()["data"]["accessToken"]

        assert self._srp_login(old).status_code == 401
        assert self._password_login(old).status_code == 401

    def test_a_live_session_alone_cannot_change_the_password(self):
        refused = self._change("a-brand-new-password", None)
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["name"] == "SudoRequiredError"

        assert self._password_login(self.user.password).status_code == 200

    def test_a_ticket_for_something_else_does_not_change_it(self):
        refused = self._change("a-brand-new-password", self._ticket("2fa:settings"))
        assert refused.status_code == 403, refused.text

        assert self._password_login(self.user.password).status_code == 200

    def test_a_ticket_changes_the_password_once(self):
        ticket = self._ticket()
        assert self._change("first-new-password", ticket).status_code == 200

        replay = self._change("second-new-password", ticket)
        assert replay.status_code == 403, replay.text
        assert self._srp_login("first-new-password").status_code == 200

    def test_nobody_else_can_change_it(self):
        other = self.user_client.create_user()
        other_token = self.user_client.login(
            self.client, other.username, other.password
        )
        other_headers = {"Authorization": f"Bearer {other_token}"}
        other_ticket = self.client.post(
            "/users/auth/sudo",
            headers=other_headers,
            json={
                "method": "password",
                "credentials": {"password": other.password},
                "purpose": "password:change",
            },
        ).json()["data"]["sudoTicket"]

        refused = self._change(
            "a-brand-new-password", other_ticket, headers=other_headers
        )
        assert refused.status_code == 403, refused.text
        assert self._password_login(self.user.password).status_code == 200

    @pytest.mark.parametrize(
        "field", ["srpSalt", "srpVerifier"], ids=["salt", "verifier"]
    )
    def test_only_hex_srp_values_are_accepted(self, field: str):
        salt, verifier = _client_register(self.user.username, "a-new-password")
        body = {"srpSalt": salt, "srpVerifier": verifier, "sudoTicket": self._ticket()}
        body[field] = "not:hex"

        refused = self.client.patch(
            f"/users/{self.user.user_id}/password", headers=self.headers, json=body
        )
        assert refused.status_code == 400, refused.text
        assert self._password_login(self.user.password).status_code == 200
