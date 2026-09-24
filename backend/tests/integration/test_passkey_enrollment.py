"""Someone who has just signed in can add a passkey without proving it again.

A finished sign-in hands back a ticket for adding a passkey. It has to be as
narrow as the ticket the sudo page would have issued: this account, adding a
passkey, once, within a few minutes.
"""

from datetime import timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import CreatedUser, UserCreator


def _sign_in(api_client: TestClient, user: CreatedUser) -> dict:
    resp = api_client.post(
        "/users/auth/login",
        json={"username": user.username, "password": user.password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _start_registration(
    api_client: TestClient, user: CreatedUser, token: str, ticket: str
):
    return api_client.post(
        f"/users/{user.user_id}/passkeys/options",
        headers={"Authorization": f"Bearer {token}"},
        json={"sudoTicket": ticket},
    )


@pytest.fixture
def signed_in(api_client: TestClient, user_client: UserCreator):
    user = user_client.create_user()
    data = _sign_in(api_client, user)
    return user, data["accessToken"], data["passkeyEnrollment"]["ticket"]


class TestTheTicketFromAPasswordSignIn:
    def test_starts_adding_a_passkey(self, api_client: TestClient, signed_in):
        user, token, ticket = signed_in

        resp = _start_registration(api_client, user, token, ticket)

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["options"]["challenge"]

    def test_works_only_once(self, api_client: TestClient, signed_in):
        user, token, ticket = signed_in
        assert _start_registration(api_client, user, token, ticket).status_code == 200

        replay = _start_registration(api_client, user, token, ticket)

        assert replay.status_code == 403, replay.text
        assert replay.json()["error"]["name"] == "SudoRequiredError"

    def test_is_good_for_nothing_but_adding_a_passkey(
        self, api_client: TestClient, signed_in
    ):
        user, token, ticket = signed_in
        headers = {"Authorization": f"Bearer {token}"}

        delete = api_client.request(
            "DELETE",
            f"/users/{user.user_id}/passkeys/some-credential-id",
            headers=headers,
            json={"sudoTicket": ticket},
        )
        enable_2fa = api_client.post(
            f"/users/{user.user_id}/2fa/enable",
            headers=headers,
            json={"sudoTicket": ticket},
        )
        change_password = api_client.patch(
            f"/users/{user.user_id}/password",
            headers=headers,
            json={"sudoTicket": ticket, "password": "another-Passw0rd1"},
        )

        for refused in (delete, enable_2fa, change_password):
            assert refused.status_code == 403, refused.text
            assert refused.json()["error"]["name"] == "SudoRequiredError"

    def test_is_good_only_for_the_account_that_signed_in(
        self, api_client: TestClient, user_client: UserCreator, signed_in
    ):
        _, _, ticket = signed_in
        other = user_client.create_user()
        other_token = _sign_in(api_client, other)["accessToken"]

        resp = _start_registration(api_client, other, other_token, ticket)

        assert resp.status_code == 403, resp.text

    def test_lasts_a_few_minutes(
        self, api_client: TestClient, user_client: UserCreator, monkeypatch
    ):
        import app.common.auth as auth

        real_now = auth._utcnow

        def signed_in_ago(delta: timedelta) -> tuple[CreatedUser, str, str]:
            user = user_client.create_user()
            monkeypatch.setattr(auth, "_utcnow", lambda: real_now() - delta)
            data = _sign_in(api_client, user)
            monkeypatch.setattr(auth, "_utcnow", real_now)
            return user, data["accessToken"], data["passkeyEnrollment"]["ticket"]

        recent = signed_in_ago(timedelta(minutes=2))
        assert _start_registration(api_client, *recent).status_code == 200

        stale = signed_in_ago(timedelta(minutes=6))
        refused = _start_registration(api_client, *stale)
        assert refused.status_code == 403, refused.text


class TestTheTicketFromATwoStepSignIn:
    @pytest.fixture
    def two_step_user(self, api_client: TestClient, user_client: UserCreator):
        user = user_client.create_user()
        token = _sign_in(api_client, user)["accessToken"]
        headers = {"Authorization": f"Bearer {token}"}
        sudo = api_client.post(
            "/users/auth/sudo",
            headers=headers,
            json={
                "method": "password",
                "credentials": {"password": user.password},
                "purpose": "2fa:enable",
            },
        )
        offer = api_client.post(
            f"/users/{user.user_id}/2fa/enable",
            headers=headers,
            json={"sudoTicket": sudo.json()["data"]["sudoTicket"]},
        )
        secret = offer.json()["data"]["secret"]
        confirm = api_client.post(
            f"/users/{user.user_id}/2fa/enable",
            headers=headers,
            json={"secret": secret, "code": pyotp.TOTP(secret).now()},
        )
        assert confirm.status_code == 200, confirm.text
        yield user, secret
        import redis

        from app.core.config import settings
        from app.domain.user.login_security import (
            TWO_FACTOR_ATTEMPTS_PREFIX,
            TWO_FACTOR_LOCKOUT_PREFIX,
        )

        r = redis.Redis.from_url(settings.redis_url)
        r.delete(
            f"{TWO_FACTOR_ATTEMPTS_PREFIX}{user.user_id}",
            f"{TWO_FACTOR_LOCKOUT_PREFIX}{user.user_id}",
        )
        r.close()

    def test_comes_only_once_the_second_step_is_done(
        self, api_client: TestClient, two_step_user
    ):
        user, secret = two_step_user

        halfway = _sign_in(api_client, user)
        assert halfway["requires2FA"] is True
        assert "passkeyEnrollment" not in halfway

        done = api_client.post(
            "/users/auth/verify-2fa",
            json={
                "temp_token": halfway["tempToken"],
                "code": pyotp.TOTP(secret).now(),
            },
        )
        assert done.status_code == 200, done.text
        data = done.json()["data"]

        resp = _start_registration(
            api_client, user, data["accessToken"], data["passkeyEnrollment"]["ticket"]
        )
        assert resp.status_code == 200, resp.text
