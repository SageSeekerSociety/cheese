"""Registration codes, password limits and the login budget, through the API."""

import re
import time
import uuid

import pyotp
import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import CreatedUser, UserCreator
from tests.support.consent import SIGNUP_CONSENT
from tests.support.outbox import Outbox, install_outbox

OVERLONG = "a1!" * 24 + "b"  # 73 bytes: one past what bcrypt can take


@pytest.fixture
def outbox(monkeypatch) -> Outbox:
    return install_outbox(monkeypatch)


@pytest.fixture
def forget_redis_state():
    """Lockouts outlive the test and its rolled-back users."""
    from app.domain.user.login_security import (
        LOGIN_FAILURES_PREFIX,
        LOGIN_WAIT_PREFIX,
        STEP_UP_PASSWORD_ATTEMPTS_PREFIX,
        STEP_UP_PASSWORD_LOCKOUT_PREFIX,
        TWO_FACTOR_ATTEMPTS_PREFIX,
        TWO_FACTOR_LOCKOUT_PREFIX,
    )

    users: list[CreatedUser] = []
    yield users.append

    import redis

    from app.core.config import settings

    r = redis.Redis.from_url(settings.redis_url)
    for user in users:
        r.delete(
            f"{LOGIN_FAILURES_PREFIX}{user.username}",
            f"{LOGIN_WAIT_PREFIX}{user.username}",
            *(
                f"{p}{user.user_id}"
                for p in (
                    STEP_UP_PASSWORD_ATTEMPTS_PREFIX,
                    STEP_UP_PASSWORD_LOCKOUT_PREFIX,
                    TWO_FACTOR_ATTEMPTS_PREFIX,
                    TWO_FACTOR_LOCKOUT_PREFIX,
                )
            ),
        )
    r.close()


def _enable_2fa(
    client: TestClient, user: CreatedUser, headers: dict[str, str]
) -> pyotp.TOTP:
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
    return pyotp.TOTP(secret)


def _login(client: TestClient, username: str, password: str):
    return client.post(
        "/users/auth/login", json={"username": username, "password": password}
    )


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
        self, api_client: TestClient, outbox: Outbox
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
        self, api_client: TestClient, outbox: Outbox
    ):
        email = f"reg-{uuid.uuid4().hex[:12]}@example.com"
        assert (
            api_client.post("/users/verify/email", json={"email": email}).status_code
            == 200
        )
        code = outbox.code()
        wrong = "000000" if code != "000000" else "111111"

        for _ in range(5):
            resp = api_client.post("/users", json=_registration(email, wrong))
            assert resp.status_code == 422, resp.text

        resp = api_client.post("/users", json=_registration(email, code))
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["data"]["reason"] == "invalid_email_code"

    def test_the_right_code_still_works_after_fewer_misses(
        self, api_client: TestClient, outbox: Outbox
    ):
        email = f"reg-{uuid.uuid4().hex[:12]}@example.com"
        api_client.post("/users/verify/email", json={"email": email})
        code = outbox.code()
        wrong = "000000" if code != "000000" else "111111"

        for _ in range(4):
            api_client.post("/users", json=_registration(email, wrong))

        resp = api_client.post("/users", json=_registration(email, code))
        assert resp.status_code == 200, resp.text

    def test_an_overlong_password_is_refused_without_spending_the_code(
        self, api_client: TestClient, outbox: Outbox
    ):
        email = f"reg-{uuid.uuid4().hex[:12]}@example.com"
        api_client.post("/users/verify/email", json={"email": email})
        code = outbox.code()

        refused = api_client.post(
            "/users", json=_registration(email, code, password=OVERLONG)
        )
        assert refused.status_code == 400, refused.text

        accepted = api_client.post("/users", json=_registration(email, code))
        assert accepted.status_code == 200, accepted.text

    def test_the_sixth_code_within_an_hour_is_refused(
        self, api_client: TestClient, outbox: Outbox, monkeypatch
    ):
        import app.domain.user.mail_quota as mail_quota

        monkeypatch.setattr(mail_quota, "MAIL_COOLDOWN_SECONDS", 0)
        email = f"reg-{uuid.uuid4().hex[:12]}@example.com"

        for _ in range(5):
            resp = api_client.post("/users/verify/email", json={"email": email})
            assert resp.status_code == 200, resp.text

        refused = api_client.post("/users/verify/email", json={"email": email})
        assert refused.status_code == 400, refused.text
        data = refused.json()["error"]["data"]
        assert data["reason"] == "email_code_too_soon"
        assert 0 < data["retryAfterSeconds"] <= 60 * 60
        assert len(outbox.sent) == 5


RECOVER = "/users/recover/password/request"


def _recover(client: TestClient, email: str):
    return client.post(RECOVER, json={"email": email})


def _unknown_email() -> str:
    return f"nobody-{uuid.uuid4().hex[:12]}@example.com"


class TestRecoveryMail:
    def test_a_second_request_within_a_minute_sends_nothing(
        self, api_client: TestClient, user_client: UserCreator, outbox: Outbox
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
        outbox: Outbox,
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
        self, api_client: TestClient, user_client: UserCreator, outbox: Outbox
    ):
        first = user_client.create_user()
        second = user_client.create_user()

        _recover(api_client, first.email)
        outbox.wait_for(1)
        _recover(api_client, second.email)

        outbox.settle(2)
        assert {m["to"] for m in outbox.sent} == {first.email, second.email}

    def test_case_and_whitespace_variants_share_the_limit(
        self, api_client: TestClient, user_client: UserCreator, outbox: Outbox
    ):
        user = user_client.create_user()
        _recover(api_client, user.email)
        outbox.wait_for(1)

        for variant in (user.email.upper(), f"  {user.email}  "):
            assert _recover(api_client, variant).status_code == 200

        outbox.settle(1)

    def test_known_and_unknown_addresses_get_the_same_answer(
        self, api_client: TestClient, user_client: UserCreator, outbox: Outbox
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
        self, api_client: TestClient, user_client: UserCreator, outbox: Outbox
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
        self, api_client: TestClient, user_client: UserCreator, outbox: Outbox
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
        from app.domain.user.login_security import LOGIN_FREE_FAILURES

        user = user_client.create_user()
        forget_redis_state(user)

        for _ in range(LOGIN_FREE_FAILURES):
            resp = _login(api_client, user.username, OVERLONG)
            assert resp.status_code == 401, resp.text

        assert _login(api_client, user.username, user.password).status_code == 403

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
        outbox: Outbox,
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

        new_password = "fresh-Password1!"
        reset = api_client.post(path, json={"token": token, "password": new_password})
        assert reset.status_code == 200, reset.text
        assert _login(api_client, user.username, new_password).status_code == 200


class TestRecoveryPasswordRule:
    @pytest.mark.parametrize(
        "password", ["sh0rt!a", "letters0nly", "12345678!", "no-digits!"], ids=str
    )
    def test_a_weak_password_is_refused_and_the_link_still_works(
        self,
        password: str,
        api_client: TestClient,
        user_client: UserCreator,
        outbox: Outbox,
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
            path, json={"token": match.group(1), "password": "fresh-Password1!"}
        )
        assert reset.status_code == 200, reset.text


def _wait_of(resp) -> int | None:
    return (resp.json()["error"].get("data") or {}).get("retryAfterSeconds")


class TestLoginWait:
    def test_repeated_failures_start_a_wait_that_refuses_the_right_password(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        forget_redis_state,
    ):
        from app.domain.user.login_security import LOGIN_FREE_FAILURES

        user = user_client.create_user()
        forget_redis_state(user)

        for _ in range(LOGIN_FREE_FAILURES - 1):
            resp = _login(api_client, user.username, "wrong-Password!")
            assert resp.status_code == 401, resp.text
            assert _wait_of(resp) is None

        last = _login(api_client, user.username, "wrong-Password!")
        assert last.status_code == 401, last.text
        assert _wait_of(last) > 0

        right = _login(api_client, user.username, user.password)
        assert right.status_code == 403, right.text
        assert right.json()["error"]["data"]["reason"] == "too_many_attempts"
        assert 0 < _wait_of(right) <= _wait_of(last)

    def test_a_right_password_forgets_the_failures(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        forget_redis_state,
    ):
        from app.domain.user.login_security import LOGIN_FREE_FAILURES

        user = user_client.create_user()
        forget_redis_state(user)

        for _ in range(LOGIN_FREE_FAILURES - 1):
            _login(api_client, user.username, "wrong-Password!")
        assert _login(api_client, user.username, user.password).status_code == 200

        for _ in range(LOGIN_FREE_FAILURES - 1):
            resp = _login(api_client, user.username, "wrong-Password!")
            assert resp.status_code == 401, resp.text
            assert _wait_of(resp) is None

    def test_a_stranger_cannot_keep_the_owner_out(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        forget_redis_state,
        monkeypatch,
    ):
        """However many wrong passwords somebody sends for the account, its
        owner waits no longer than the cap and is then let in."""

        from app.domain.user import login_security

        monkeypatch.setattr(login_security, "LOGIN_FIRST_WAIT_SECONDS", 1)
        monkeypatch.setattr(login_security, "LOGIN_MAX_WAIT_SECONDS", 1)
        user = user_client.create_user()
        forget_redis_state(user)

        for _ in range(login_security.LOGIN_FREE_FAILURES + 30):
            resp = _login(api_client, user.username, "wrong-Password!")
            assert resp.status_code in (401, 403), resp.text
            assert (_wait_of(resp) or 0) <= 1

        time.sleep(1.1)
        assert _login(api_client, user.username, user.password).status_code == 200

    def test_stopping_at_the_2fa_prompt_spends_nothing(
        self,
        api_client: TestClient,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
        forget_redis_state,
    ):
        user = authenticated_user
        forget_redis_state(user)
        _enable_2fa(api_client, user, auth_headers)

        for _ in range(6):
            resp = _login(api_client, user.username, user.password)
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["requires2FA"] is True


PROXY = "10.255.0.1"


@pytest.fixture
def from_address(app, api_client: TestClient, _portal, monkeypatch):
    """Clients that each reach the server from an address of their own, behind
    a proxy the server trusts. The address a client is given here is the one
    uvicorn would have resolved from the proxy's X-Forwarded-For."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", PROXY)
    clients: list[TestClient] = []
    addresses: list[str] = []

    def make(address: str | None = None) -> TestClient:
        if address is None:
            octets = uuid.uuid4().bytes
            address = f"198.18.{octets[0]}.{octets[1] % 254 + 1}"
        client = TestClient(app, base_url="http://testserver", client=(address, 50000))
        client.portal = _portal  # type: ignore[assignment]
        clients.append(client)
        addresses.append(address)
        return client

    yield make

    import redis

    from app.core.config import settings

    for client in clients:
        client.close()
    r = redis.Redis.from_url(settings.redis_url)
    for address in addresses:
        stale = list(r.scan_iter(match=f"*:{address}*"))
        if stale:
            r.delete(*stale)
    r.close()


@pytest.fixture
def few_failures(monkeypatch) -> int:
    from app.domain.user import login_security

    monkeypatch.setattr(login_security, "CLIENT_MAX_FAILURES", 3)
    return 3


def _nobody() -> str:
    return f"nobody-{uuid.uuid4().hex[:12]}"


def _refused(resp) -> bool:
    return (
        resp.status_code == 403
        and resp.json()["error"]["data"]["reason"] == "too_many_attempts"
        and resp.json()["error"]["data"]["retryAfterSeconds"] > 0
    )


class TestClientAddressLimits:
    """Per-address limits: failures only, and only where the server knows the
    client's address rather than a proxy's."""

    def test_wrong_passwords_from_one_address_are_limited_across_usernames(
        self, from_address, few_failures, user_client: UserCreator
    ):
        user = user_client.create_user()
        guesser, other = from_address(), from_address()

        for _ in range(few_failures):
            assert _login(guesser, _nobody(), "wrong-Password!").status_code == 401

        assert _refused(_login(guesser, user.username, user.password))
        assert _login(other, user.username, user.password).status_code == 200

    def test_signing_in_neither_counts_nor_clears_an_address_failures(
        self, from_address, few_failures, user_client: UserCreator
    ):
        user = user_client.create_user()
        client = from_address()

        for _ in range(few_failures - 1):
            assert _login(client, _nobody(), "wrong-Password!").status_code == 401
        for _ in range(few_failures + 1):
            assert _login(client, user.username, user.password).status_code == 200
        assert _login(client, _nobody(), "wrong-Password!").status_code == 401

        assert _refused(_login(client, _nobody(), "wrong-Password!"))

    def test_nothing_is_limited_by_address_without_trusted_proxies(
        self, from_address, few_failures, user_client: UserCreator, monkeypatch
    ):
        """Unset, every request carries the proxy's address, and a limit on it
        would refuse everybody at once."""
        monkeypatch.delenv("FORWARDED_ALLOW_IPS")
        user = user_client.create_user()
        client = from_address()

        for _ in range(few_failures * 3):
            assert _login(client, _nobody(), "wrong-Password!").status_code == 401
        assert _login(client, user.username, user.password).status_code == 200

    def test_requests_from_a_trusted_proxy_itself_are_not_limited(
        self, from_address, few_failures
    ):
        client = from_address(PROXY)

        for _ in range(few_failures * 3):
            assert _login(client, _nobody(), "wrong-Password!").status_code == 401

    def test_wrong_second_factors_from_one_address_are_limited(
        self,
        from_address,
        few_failures,
        api_client: TestClient,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
        forget_redis_state,
    ):
        user = authenticated_user
        forget_redis_state(user)
        totp = _enable_2fa(api_client, user, auth_headers)
        guesser, other = from_address(), from_address()

        def second_step(client: TestClient, code: str):
            first = _login(client, user.username, user.password)
            assert first.status_code == 200, first.text
            return client.post(
                "/users/auth/verify-2fa",
                json={"temp_token": first.json()["data"]["tempToken"], "code": code},
            )

        wrong = "000000" if totp.now() != "000000" else "111111"
        for _ in range(few_failures):
            assert second_step(guesser, wrong).status_code == 401

        assert _refused(second_step(guesser, totp.now()))
        assert second_step(other, totp.now()).status_code == 200

    def test_wrong_email_codes_from_one_address_are_limited(
        self, from_address, few_failures, outbox: Outbox
    ):
        guesser, other = from_address(), from_address()
        email = f"reg-{uuid.uuid4().hex[:12]}@example.com"
        sent = other.post("/users/verify/email", json={"email": email})
        assert sent.status_code == 200, sent.text
        code = outbox.code()
        wrong = "000000" if code != "000000" else "111111"

        for _ in range(few_failures):
            unknown = f"reg-{uuid.uuid4().hex[:12]}@example.com"
            resp = guesser.post("/users", json=_registration(unknown, wrong))
            assert resp.status_code == 422, resp.text

        assert _refused(guesser.post("/users", json=_registration(email, code)))
        assert other.post("/users", json=_registration(email, code)).status_code == 200

    def test_verification_mail_from_one_address_is_limited_across_recipients(
        self, from_address, outbox: Outbox, monkeypatch
    ):
        import app.domain.user.mail_quota as mail_quota

        monkeypatch.setattr(mail_quota, "MAIL_CLIENT_HOURLY_LIMIT", 2)
        sender, other = from_address(), from_address()

        def send(client: TestClient):
            email = f"reg-{uuid.uuid4().hex[:12]}@example.com"
            return client.post("/users/verify/email", json={"email": email})

        assert send(sender).status_code == 200
        assert send(sender).status_code == 200
        assert send(sender).status_code == 400
        assert send(other).status_code == 200
        assert len(outbox.sent) == 3

    def test_recovery_mail_from_one_address_is_limited_across_recipients(
        self, from_address, user_client: UserCreator, outbox: Outbox, monkeypatch
    ):
        import app.domain.user.mail_quota as mail_quota

        monkeypatch.setattr(mail_quota, "MAIL_CLIENT_HOURLY_LIMIT", 2)
        users = [user_client.create_user() for _ in range(4)]
        sender, other = from_address(), from_address()

        for user in users[:3]:
            assert _recover(sender, user.email).status_code == 200
        outbox.settle(2)
        assert _recover(other, users[3].email).status_code == 200

        outbox.settle(3)
        expected = {u.email for u in users[:2]} | {users[3].email}
        assert {m["to"] for m in outbox.sent} == expected
