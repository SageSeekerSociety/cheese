"""Signing in with a code mailed to the account's address, through the API."""

import time
import uuid

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.domain.user.models import User
from tests.integration.conftest import CreatedUser, UserCreator
from tests.support.outbox import Outbox, install_outbox

REQUEST = "/users/auth/email-code"
VERIFY = "/users/auth/email-code/verify"


@pytest.fixture
def outbox(monkeypatch) -> Outbox:
    return install_outbox(monkeypatch)


def _request(client: TestClient, email: str):
    return client.post(REQUEST, json={"email": email})


def _verify(client: TestClient, email: str, code: str):
    return client.post(VERIFY, json={"email": email, "code": code})


def _mailed_code(client: TestClient, outbox: Outbox, email: str) -> str:
    before = len(outbox.sent)
    resp = _request(client, email)
    assert resp.status_code == 200, resp.text
    outbox.wait_for(before + 1)
    return outbox.code()


def _wrong(code: str) -> str:
    return "000000" if code != "000000" else "111111"


def _unknown_email() -> str:
    return f"nobody-{uuid.uuid4().hex[:12]}@example.com"


def _placeholder_account(db_session, _portal, user_client: UserCreator) -> CreatedUser:
    user = user_client.create_user()
    placeholder = f"oauth-github-{uuid.uuid4().hex[:8]}@placeholder.internal"

    async def make_placeholder() -> None:
        await db_session.execute(
            update(User).where(User.id == user.user_id).values(email=placeholder)
        )
        await db_session.flush()

    _portal.call(make_placeholder)
    user.email = placeholder
    return user


def _enable_2fa(client: TestClient, user: CreatedUser) -> pyotp.TOTP:
    headers = {"Authorization": f"Bearer {user.token}"}
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


class TestRequestingACode:
    def test_every_address_gets_the_same_answer_and_only_an_account_is_mailed(
        self,
        db_session,
        _portal,
        api_client: TestClient,
        user_client: UserCreator,
        outbox: Outbox,
    ):
        user = user_client.create_user()
        placeholder = _placeholder_account(db_session, _portal, user_client)

        answers = {
            (r.status_code, r.content)
            for r in (
                _request(api_client, user.email),
                _request(api_client, _unknown_email()),
                _request(api_client, placeholder.email),
            )
        }

        assert len(answers) == 1, answers
        assert answers.pop()[0] == 200
        outbox.settle(1)
        assert outbox.sent[0]["to"] == user.email

    def test_a_second_request_within_a_minute_is_refused_alike_for_any_address(
        self, api_client: TestClient, user_client: UserCreator, outbox: Outbox
    ):
        user = user_client.create_user()
        unknown = _unknown_email()
        _request(api_client, user.email)
        _request(api_client, unknown)

        known_again = _request(api_client, user.email)
        unknown_again = _request(api_client, unknown)

        for resp in (known_again, unknown_again):
            assert resp.status_code == 400, resp.text
            data = resp.json()["error"]["data"]
            assert data["reason"] == "email_code_too_soon"
            assert 0 < data["retryAfterSeconds"] <= 60
        outbox.settle(1)

    def test_the_address_is_matched_whatever_its_case(
        self, api_client: TestClient, user_client: UserCreator, outbox: Outbox
    ):
        user = user_client.create_user()

        code = _mailed_code(api_client, outbox, user.email.upper())

        assert outbox.sent[-1]["to"] == user.email
        assert _verify(api_client, user.email.upper(), code).status_code == 200


class TestSigningIn:
    def test_the_code_signs_in_and_the_sign_in_is_listed_as_by_email_code(
        self, api_client: TestClient, user_client: UserCreator, outbox: Outbox
    ):
        user = user_client.create_user()
        code = _mailed_code(api_client, outbox, user.email)

        resp = _verify(api_client, user.email, code)

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["user"]["username"] == user.username
        assert data["requires2FA"] is False
        assert data["passkeyEnrollment"]["ticket"]
        sessions = api_client.get(
            "/users/me/sessions",
            headers={"Authorization": f"Bearer {data['accessToken']}"},
        )
        assert sessions.status_code == 200, sessions.text
        current = [s for s in sessions.json()["data"]["sessions"] if s["current"]]
        assert [s["loginMethod"] for s in current] == ["email_code"]

    def test_a_code_signs_in_once(
        self, api_client: TestClient, user_client: UserCreator, outbox: Outbox
    ):
        user = user_client.create_user()
        code = _mailed_code(api_client, outbox, user.email)
        assert _verify(api_client, user.email, code).status_code == 200

        again = _verify(api_client, user.email, code)

        assert again.status_code == 401, again.text
        assert again.json()["error"]["data"]["reason"] == "invalid_email_code"

    def test_a_code_past_its_life_is_refused(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        outbox: Outbox,
        monkeypatch,
    ):
        import app.domain.user.verification_service as verification

        monkeypatch.setattr(verification, "VERIFICATION_CODE_TTL", 1)
        user = user_client.create_user()
        code = _mailed_code(api_client, outbox, user.email)

        time.sleep(1.5)
        resp = _verify(api_client, user.email, code)

        assert resp.status_code == 401, resp.text
        assert resp.json()["error"]["data"]["reason"] == "invalid_email_code"

    def test_five_wrong_codes_void_the_right_one(
        self, api_client: TestClient, user_client: UserCreator, outbox: Outbox
    ):
        user = user_client.create_user()
        code = _mailed_code(api_client, outbox, user.email)

        for _ in range(5):
            wrong = _verify(api_client, user.email, _wrong(code))
            assert wrong.status_code == 401, wrong.text
            assert wrong.json()["error"]["data"]["reason"] == "invalid_email_code"

        assert _verify(api_client, user.email, code).status_code == 401

    def test_the_right_code_still_works_after_fewer_misses(
        self, api_client: TestClient, user_client: UserCreator, outbox: Outbox
    ):
        user = user_client.create_user()
        code = _mailed_code(api_client, outbox, user.email)

        for _ in range(4):
            _verify(api_client, user.email, _wrong(code))

        assert _verify(api_client, user.email, code).status_code == 200

    def test_a_sign_up_code_does_not_sign_in(
        self, api_client: TestClient, user_client: UserCreator, outbox: Outbox
    ):
        email = f"later-{uuid.uuid4().hex[:12]}@example.com"
        sent = api_client.post("/users/verify/email", json={"email": email})
        assert sent.status_code == 200, sent.text
        sign_up_code = outbox.code()
        # The address is taken by an account after its sign-up code went out.
        user_client.create_user(email=email)

        resp = _verify(api_client, email, sign_up_code)

        assert resp.status_code == 401, resp.text

    def test_a_code_for_an_address_without_an_account_is_just_wrong(
        self, api_client: TestClient
    ):
        resp = _verify(api_client, _unknown_email(), "123456")

        assert resp.status_code == 401, resp.text
        assert resp.json()["error"]["data"]["reason"] == "invalid_email_code"


class TestTwoFactorAccounts:
    def test_the_code_leads_to_the_second_step_not_a_session(
        self,
        api_client: TestClient,
        authenticated_user: CreatedUser,
        outbox: Outbox,
    ):
        totp = _enable_2fa(api_client, authenticated_user)
        code = _mailed_code(api_client, outbox, authenticated_user.email)

        resp = _verify(api_client, authenticated_user.email, code)

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["requires2FA"] is True
        assert "accessToken" not in data
        assert not resp.headers.get_list("set-cookie")

        second = api_client.post(
            "/users/auth/verify-2fa",
            json={"temp_token": data["tempToken"], "code": totp.now()},
        )
        assert second.status_code == 200, second.text
        assert second.json()["data"]["accessToken"]
