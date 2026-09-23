"""Registration codes, password limits and the login budget, through the API."""

import re
import time
import uuid

import pyotp
import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import CreatedUser, UserCreator
from tests.support.consent import SIGNUP_CONSENT

OVERLONG = "a!" * 36 + "b"  # 73 bytes: one past what bcrypt can take


class _Outbox:
    def __init__(self, *, delivers: bool = True) -> None:
        self.is_configured = True
        self.delivers = delivers
        self.held = False
        self.sent: list[dict] = []

    async def send(self, **kwargs) -> bool:
        import asyncio

        while self.held:
            await asyncio.sleep(0.01)
        self.sent.append(kwargs)
        return self.delivers

    def wait_for(self, count: int, timeout: float = 5.0) -> None:
        """Recovery mail leaves after the response, so wait for it to land."""
        deadline = time.monotonic() + timeout
        while len(self.sent) < count:
            assert time.monotonic() < deadline, f"{len(self.sent)} of {count} sent"
            time.sleep(0.01)

    def settle(self, count: int) -> None:
        """Wait for ``count`` mails, then make sure no further one follows."""
        self.wait_for(count)
        time.sleep(0.2)
        assert len(self.sent) == count, self.sent


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
        "consent": SIGNUP_CONSENT,
        "password": password,
    }


class TestRegistrationEmailCode:
    def test_a_failed_send_is_an_error_and_can_be_retried_at_once(
        self, api_client: TestClient, outbox: _Outbox
    ):
        email = f"reg-{uuid.uuid4().hex[:12]}@example.com"

        outbox.delivers = False
        failed = api_client.post("/users/verify/email", json={"email": email})
        assert failed.status_code == 503, failed.text

        outbox.delivers = True
        retried = api_client.post("/users/verify/email", json={"email": email})
        assert retried.status_code == 200, retried.text
        assert len(outbox.sent) == 2

    def test_five_wrong_codes_void_the_right_one(
        self, api_client: TestClient, outbox: _Outbox
    ):
        email = f"reg-{uuid.uuid4().hex[:12]}@example.com"
        assert (
            api_client.post("/users/verify/email", json={"email": email}).status_code
            == 200
        )
        code = _mailed_code(outbox)
        wrong = "000000" if code != "000000" else "111111"

        for _ in range(5):
            resp = api_client.post("/users", json=_registration(email, wrong))
            assert resp.status_code == 422, resp.text

        resp = api_client.post("/users", json=_registration(email, code))
        assert resp.status_code == 422, resp.text
        assert "verification code" in resp.json()["error"]["message"]

    def test_the_right_code_still_works_after_fewer_misses(
        self, api_client: TestClient, outbox: _Outbox
    ):
        email = f"reg-{uuid.uuid4().hex[:12]}@example.com"
        api_client.post("/users/verify/email", json={"email": email})
        code = _mailed_code(outbox)
        wrong = "000000" if code != "000000" else "111111"

        for _ in range(4):
            api_client.post("/users", json=_registration(email, wrong))

        resp = api_client.post("/users", json=_registration(email, code))
        assert resp.status_code == 200, resp.text

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

    def test_the_sixth_code_within_an_hour_is_refused(
        self, api_client: TestClient, outbox: _Outbox, monkeypatch
    ):
        import app.domain.user.mail_quota as mail_quota

        monkeypatch.setattr(mail_quota, "MAIL_COOLDOWN_SECONDS", 0)
        email = f"reg-{uuid.uuid4().hex[:12]}@example.com"

        for _ in range(5):
            resp = api_client.post("/users/verify/email", json={"email": email})
            assert resp.status_code == 200, resp.text

        refused = api_client.post("/users/verify/email", json={"email": email})
        assert refused.status_code == 400, refused.text
        assert len(outbox.sent) == 5


RECOVER = "/users/recover/password/request"


def _recover(client: TestClient, email: str):
    return client.post(RECOVER, json={"email": email})


def _unknown_email() -> str:
    return f"nobody-{uuid.uuid4().hex[:12]}@example.com"


class TestRecoveryMail:
    def test_a_second_request_within_a_minute_sends_nothing(
        self, api_client: TestClient, user_client: UserCreator, outbox: _Outbox
    ):
        user = user_client.create_user()

        assert _recover(api_client, user.email).status_code == 200
        outbox.wait_for(1)
        assert _recover(api_client, user.email).status_code == 200

        outbox.settle(1)
        assert outbox.sent[0]["to"] == user.email

    def test_the_sixth_request_within_an_hour_sends_nothing(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        outbox: _Outbox,
        monkeypatch,
    ):
        import app.domain.user.mail_quota as mail_quota

        monkeypatch.setattr(mail_quota, "MAIL_COOLDOWN_SECONDS", 0)
        user = user_client.create_user()

        for sent in range(1, 6):
            assert _recover(api_client, user.email).status_code == 200
            outbox.wait_for(sent)
        assert _recover(api_client, user.email).status_code == 200

        outbox.settle(5)

    def test_addresses_have_separate_limits(
        self, api_client: TestClient, user_client: UserCreator, outbox: _Outbox
    ):
        first = user_client.create_user()
        second = user_client.create_user()

        _recover(api_client, first.email)
        outbox.wait_for(1)
        _recover(api_client, second.email)

        outbox.settle(2)
        assert {m["to"] for m in outbox.sent} == {first.email, second.email}

    def test_case_and_whitespace_variants_share_the_limit(
        self, api_client: TestClient, user_client: UserCreator, outbox: _Outbox
    ):
        user = user_client.create_user()
        _recover(api_client, user.email)
        outbox.wait_for(1)

        for variant in (user.email.upper(), f"  {user.email}  "):
            assert _recover(api_client, variant).status_code == 200

        outbox.settle(1)

    def test_known_and_unknown_addresses_get_the_same_answer(
        self, api_client: TestClient, user_client: UserCreator, outbox: _Outbox
    ):
        user = user_client.create_user()
        unknown = _unknown_email()

        known_first = _recover(api_client, user.email)
        unknown_first = _recover(api_client, unknown)
        known_limited = _recover(api_client, user.email)
        unknown_limited = _recover(api_client, unknown)

        answers = {
            (r.status_code, r.content)
            for r in (known_first, unknown_first, known_limited, unknown_limited)
        }
        assert len(answers) == 1, answers
        assert known_first.status_code == 200
        outbox.settle(1)
        assert outbox.sent[0]["to"] == user.email

    def test_the_answer_does_not_wait_for_the_mail(
        self, api_client: TestClient, user_client: UserCreator, outbox: _Outbox
    ):
        user = user_client.create_user()
        outbox.held = True
        try:
            resp = _recover(api_client, user.email)
            assert resp.status_code == 200, resp.text
            assert outbox.sent == []
        finally:
            outbox.held = False

        outbox.wait_for(1)
        match = re.search(r"token=([\w.-]+)", outbox.sent[0]["body_text"])
        assert match, outbox.sent[0]

    def test_a_failed_send_still_answers_the_same(
        self, api_client: TestClient, user_client: UserCreator, outbox: _Outbox
    ):
        user = user_client.create_user()
        outbox.delivers = False

        failed = _recover(api_client, user.email)
        outbox.wait_for(1)
        unknown = _recover(api_client, _unknown_email())

        assert (failed.status_code, failed.content) == (
            unknown.status_code,
            unknown.content,
        )


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

    def test_resetting_to_one_is_refused_and_the_link_still_works(
        self,
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
        outbox.wait_for(1)
        match = re.search(r"token=([\w.-]+)", outbox.sent[-1]["body_text"])
        assert match, outbox.sent[-1]
        token = match.group(1)

        path = "/users/recover/password/verify"
        refused = api_client.post(path, json={"token": token, "password": OVERLONG})
        assert refused.status_code == 400, refused.text

        new_password = "fresh-Password!"
        reset = api_client.post(path, json={"token": token, "password": new_password})
        assert reset.status_code == 200, reset.text
        assert _login(api_client, user.username, new_password).status_code == 200


class TestRecoveryPasswordRule:
    @pytest.mark.parametrize(
        "password", ["short!a", "lettersonly", "12345678!"], ids=str
    )
    def test_a_weak_password_is_refused_and_the_link_still_works(
        self,
        password: str,
        api_client: TestClient,
        user_client: UserCreator,
        outbox: _Outbox,
        forget_redis_state,
    ):
        user = user_client.create_user()
        forget_redis_state(user)
        api_client.post("/users/recover/password/request", json={"email": user.email})
        outbox.wait_for(1)
        match = re.search(r"token=([\w.-]+)", outbox.sent[-1]["body_text"])
        assert match, outbox.sent[-1]
        path = "/users/recover/password/verify"

        refused = api_client.post(
            path, json={"token": match.group(1), "password": password}
        )
        assert refused.status_code == 422, refused.text
        assert _login(api_client, user.username, password).status_code == 401

        reset = api_client.post(
            path, json={"token": match.group(1), "password": "fresh-Password!"}
        )
        assert reset.status_code == 200, reset.text


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
        sudo = api_client.post(
            "/users/auth/sudo",
            headers=auth_headers,
            json={
                "method": "password",
                "credentials": {"password": user.password},
                "purpose": "2fa:enable",
            },
        )
        assert sudo.status_code == 200, sudo.text
        init = api_client.post(
            f"/users/{user.user_id}/2fa/enable",
            headers=auth_headers,
            json={"sudoTicket": sudo.json()["data"]["sudoTicket"]},
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
