"""Registration codes, password limits and the login budget, through the API."""

import re
import uuid

import pyotp
import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import CreatedUser, UserCreator

OVERLONG = "a!" * 36 + "b"  # 73 bytes: one past what bcrypt can take


class _Outbox:
    def __init__(self, *, delivers: bool = True) -> None:
        self.is_configured = True
        self.delivers = delivers
        self.sent: list[dict] = []

    async def send(self, **kwargs) -> bool:
        self.sent.append(kwargs)
        return self.delivers


@pytest.fixture
def outbox(monkeypatch) -> _Outbox:
    import app.core.email as email_module
    import app.domain.user.verification_service as verification_module

    box = _Outbox()
    monkeypatch.setattr(email_module, "get_email_sender", lambda: box)
    monkeypatch.setattr(verification_module, "get_email_sender", lambda: box)
    return box


@pytest.fixture
def forget_redis_state():
    """Lockouts and TOTP secrets outlive the test and its rolled-back users."""
    from app.domain.user.login_security import (
        LOGIN_ATTEMPTS_PREFIX,
        LOGIN_LOCKOUT_PREFIX,
        STEP_UP_PASSWORD_ATTEMPTS_PREFIX,
        STEP_UP_PASSWORD_LOCKOUT_PREFIX,
        TOTP_ALWAYS_PREFIX,
        TOTP_BACKUP_PREFIX,
        TOTP_SECRET_PREFIX,
    )

    users: list[CreatedUser] = []
    yield users.append

    import redis

    from app.core.config import settings

    r = redis.Redis.from_url(settings.redis_url)
    for user in users:
        r.delete(
            f"{LOGIN_ATTEMPTS_PREFIX}{user.username}",
            f"{LOGIN_LOCKOUT_PREFIX}{user.username}",
            *(
                f"{p}{user.user_id}"
                for p in (
                    TOTP_SECRET_PREFIX,
                    TOTP_BACKUP_PREFIX,
                    TOTP_ALWAYS_PREFIX,
                    STEP_UP_PASSWORD_ATTEMPTS_PREFIX,
                    STEP_UP_PASSWORD_LOCKOUT_PREFIX,
                )
            ),
        )
    r.close()


def _login(client: TestClient, username: str, password: str):
    return client.post(
        "/users/auth/login", json={"username": username, "password": password}
    )


def _mailed_code(outbox: _Outbox) -> str:
    match = re.search(r"\b(\d{6})\b", outbox.sent[-1]["body_text"])
    assert match, outbox.sent[-1]
    return match.group(1)


def _registration(email: str, code: str, password: str = "abc123456Test!") -> dict:
    suffix = uuid.uuid4().hex[:10]
    return {
        "username": f"reg-{suffix}",
        "nickname": f"reg{suffix}",
        "email": email,
        "emailCode": code,
        "password": password,
    }


class TestRegistrationEmailCode:
    def test_an_overlong_password_is_refused_without_spending_the_code(
        self, api_client: TestClient, outbox: _Outbox
    ):
        email = f"reg-{uuid.uuid4().hex[:12]}@example.com"
        api_client.post("/users/verify/email", json={"email": email})
        code = _mailed_code(outbox)

        refused = api_client.post(
            "/users", json=_registration(email, code, password=OVERLONG)
        )
        assert refused.status_code == 400, refused.text

        accepted = api_client.post("/users", json=_registration(email, code))
        assert accepted.status_code == 200, accepted.text


class TestOverlongPasswords:
    def test_logging_in_with_one_is_a_wrong_password_that_counts(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        forget_redis_state,
    ):
        user = user_client.create_user()
        forget_redis_state(user)

        resp = _login(api_client, user.username, OVERLONG)

        assert resp.status_code == 401, resp.text
        assert "4 attempts remaining" in resp.json()["error"]["message"]

    def test_re_authenticating_with_one_is_a_wrong_password(
        self,
        api_client: TestClient,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
        forget_redis_state,
    ):
        forget_redis_state(authenticated_user)

        resp = api_client.post(
            "/users/auth/sudo",
            headers=auth_headers,
            json={"method": "password", "credentials": {"password": OVERLONG}},
        )

        assert resp.status_code == 401, resp.text

    @pytest.mark.parametrize(
        "path", ["/users/password/reset", "/users/recover/password/verify"]
    )
    def test_resetting_to_one_is_refused_and_the_link_still_works(
        self,
        path: str,
        api_client: TestClient,
        user_client: UserCreator,
        outbox: _Outbox,
        forget_redis_state,
    ):
        user = user_client.create_user()
        forget_redis_state(user)
        requested = api_client.post(
            "/users/recover/password/request", json={"email": user.email}
        )
        assert requested.status_code == 200, requested.text
        match = re.search(r"token=([\w.-]+)", outbox.sent[-1]["body_text"])
        assert match, outbox.sent[-1]
        token = match.group(1)

        refused = api_client.post(path, json={"token": token, "password": OVERLONG})
        assert refused.status_code == 400, refused.text

        new_password = "fresh-Password!"
        reset = api_client.post(path, json={"token": token, "password": new_password})
        assert reset.status_code == 200, reset.text
        assert _login(api_client, user.username, new_password).status_code == 200


class TestLoginBudget:
    def test_the_budget_counts_down_locks_and_refuses_the_right_password(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        forget_redis_state,
    ):
        user = user_client.create_user()
        forget_redis_state(user)

        for left in (4, 3, 2, 1):
            resp = _login(api_client, user.username, "wrong-Password!")
            assert resp.status_code == 401, resp.text
            assert f"{left} attempts remaining" in resp.json()["error"]["message"]

        fifth = _login(api_client, user.username, "wrong-Password!")
        assert fifth.status_code == 403, fifth.text

        right = _login(api_client, user.username, user.password)
        assert right.status_code == 403, right.text
        assert "Try again in" in right.json()["error"]["message"]

    def test_a_right_password_gives_the_budget_back(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        forget_redis_state,
    ):
        user = user_client.create_user()
        forget_redis_state(user)

        for _ in range(4):
            _login(api_client, user.username, "wrong-Password!")
        assert _login(api_client, user.username, user.password).status_code == 200

        resp = _login(api_client, user.username, "wrong-Password!")
        assert "4 attempts remaining" in resp.json()["error"]["message"]

    def test_stopping_at_the_2fa_prompt_spends_nothing(
        self,
        api_client: TestClient,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
        forget_redis_state,
    ):
        user = authenticated_user
        forget_redis_state(user)
        init = api_client.post(
            f"/users/{user.user_id}/2fa/enable", headers=auth_headers, json={}
        )
        secret = init.json()["data"]["secret"]
        confirm = api_client.post(
            f"/users/{user.user_id}/2fa/enable",
            headers=auth_headers,
            json={"secret": secret, "code": pyotp.TOTP(secret).now()},
        )
        assert confirm.status_code == 200, confirm.text

        for _ in range(6):
            resp = _login(api_client, user.username, user.password)
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["requires2FA"] is True
