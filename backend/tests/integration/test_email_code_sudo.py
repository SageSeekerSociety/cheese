"""Confirming identity with a code mailed to the account, through the API."""

import uuid

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.domain.user.models import User
from tests.integration.conftest import CreatedUser, UserCreator
from tests.support.outbox import Outbox, install_outbox

SEND = "/users/me/sudo/email-code"
SUDO = "/users/auth/sudo"
NEW_PASSWORD = "Another#Pass123"


@pytest.fixture
def outbox(monkeypatch) -> Outbox:
    return install_outbox(monkeypatch)


def _headers(user: CreatedUser) -> dict[str, str]:
    return {"Authorization": f"Bearer {user.token}"}


def _signed_in(client: TestClient, user_client: UserCreator) -> CreatedUser:
    user = user_client.create_user()
    user.token = user_client.login(client, user.username, user.password)
    return user


def _mailed_code(client: TestClient, outbox: Outbox, user: CreatedUser) -> str:
    before = len(outbox.sent)
    resp = client.post(SEND, headers=_headers(user))
    assert resp.status_code == 200, resp.text
    outbox.wait_for(before + 1)
    assert outbox.sent[-1]["to"] == user.email
    return outbox.code()


def _confirm(client: TestClient, user: CreatedUser, code: str):
    return client.post(
        SUDO,
        headers=_headers(user),
        json={
            "method": "email_code",
            "credentials": {"code": code},
            "purpose": "password:change",
        },
    )


def _email_code_offered(client: TestClient, user: CreatedUser) -> bool:
    resp = client.get("/users/me/auth-methods", headers=_headers(user))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["emailCode"]


def _enable_2fa(client: TestClient, user: CreatedUser) -> None:
    sudo = client.post(
        SUDO,
        headers=_headers(user),
        json={
            "method": "password",
            "credentials": {"password": user.password},
            "purpose": "2fa:enable",
        },
    )
    init = client.post(
        f"/users/{user.user_id}/2fa/enable",
        headers=_headers(user),
        json={"sudoTicket": sudo.json()["data"]["sudoTicket"]},
    )
    secret = init.json()["data"]["secret"]
    confirm = client.post(
        f"/users/{user.user_id}/2fa/enable",
        headers=_headers(user),
        json={"secret": secret, "code": pyotp.TOTP(secret).now()},
    )
    assert confirm.status_code == 200, confirm.text


def _wrong(code: str) -> str:
    return "000000" if code != "000000" else "111111"


class TestWithoutTwoFactor:
    def test_the_code_confirms_identity_for_the_operation_asked(
        self, api_client: TestClient, user_client: UserCreator, outbox: Outbox
    ):
        user = _signed_in(api_client, user_client)
        assert _email_code_offered(api_client, user) is True
        code = _mailed_code(api_client, outbox, user)

        confirmed = _confirm(api_client, user, code)

        assert confirmed.status_code == 200, confirmed.text
        changed = api_client.patch(
            f"/users/{user.user_id}/password",
            headers=_headers(user),
            json={
                "password": NEW_PASSWORD,
                "sudoTicket": confirmed.json()["data"]["sudoTicket"],
            },
        )
        assert changed.status_code == 200, changed.text

    def test_an_account_with_no_other_way_can_confirm_by_email(
        self,
        db_session,
        _portal,
        api_client: TestClient,
        user_client: UserCreator,
        outbox: Outbox,
    ):
        user = _signed_in(api_client, user_client)

        async def drop_password() -> None:
            await db_session.execute(
                update(User).where(User.id == user.user_id).values(hashed_password=None)
            )
            await db_session.flush()

        _portal.call(drop_password)
        code = _mailed_code(api_client, outbox, user)

        assert _confirm(api_client, user, code).status_code == 200

    def test_a_wrong_code_is_refused_and_five_of_them_void_the_right_one(
        self, api_client: TestClient, user_client: UserCreator, outbox: Outbox
    ):
        user = _signed_in(api_client, user_client)
        code = _mailed_code(api_client, outbox, user)

        for _ in range(5):
            wrong = _confirm(api_client, user, _wrong(code))
            assert wrong.status_code == 401, wrong.text
            assert wrong.json()["error"]["data"]["reason"] == "invalid_email_code"

        assert _confirm(api_client, user, code).status_code == 401

    def test_a_sign_in_code_does_not_confirm_identity(
        self, api_client: TestClient, user_client: UserCreator, outbox: Outbox
    ):
        user = _signed_in(api_client, user_client)
        api_client.post("/users/auth/email-code", json={"email": user.email})
        outbox.wait_for(1)

        assert _confirm(api_client, user, outbox.code()).status_code == 401

    def test_asking_again_within_a_minute_is_refused_with_the_wait(
        self, api_client: TestClient, user_client: UserCreator, outbox: Outbox
    ):
        user = _signed_in(api_client, user_client)
        _mailed_code(api_client, outbox, user)

        again = api_client.post(SEND, headers=_headers(user))

        assert again.status_code == 400, again.text
        data = again.json()["error"]["data"]
        assert data["reason"] == "email_code_too_soon"
        assert 0 < data["retryAfterSeconds"] <= 60

    def test_asking_needs_a_signed_in_caller(self, api_client: TestClient):
        assert api_client.post(SEND).status_code == 401


class TestWithTwoFactor:
    def test_no_code_is_sent_and_none_is_offered(
        self, api_client: TestClient, user_client: UserCreator, outbox: Outbox
    ):
        user = _signed_in(api_client, user_client)
        _enable_2fa(api_client, user)

        refused = api_client.post(SEND, headers=_headers(user))

        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["data"]["reason"] == "email_code_unavailable"
        assert _email_code_offered(api_client, user) is False
        outbox.settle(0)

    def test_a_code_sent_before_two_step_verification_no_longer_confirms(
        self, api_client: TestClient, user_client: UserCreator, outbox: Outbox
    ):
        user = _signed_in(api_client, user_client)
        code = _mailed_code(api_client, outbox, user)
        _enable_2fa(api_client, user)

        refused = _confirm(api_client, user, code)

        assert refused.status_code == 403, refused.text


class TestPlaceholderAddress:
    def test_no_code_is_sent_and_none_is_offered(
        self,
        db_session,
        _portal,
        api_client: TestClient,
        user_client: UserCreator,
        outbox: Outbox,
    ):
        user = _signed_in(api_client, user_client)
        placeholder = f"oauth-github-{uuid.uuid4().hex[:8]}@placeholder.internal"

        async def make_placeholder() -> None:
            await db_session.execute(
                update(User).where(User.id == user.user_id).values(email=placeholder)
            )
            await db_session.flush()

        _portal.call(make_placeholder)

        refused = api_client.post(SEND, headers=_headers(user))

        assert refused.status_code == 403, refused.text
        assert _email_code_offered(api_client, user) is False
        assert _confirm(api_client, user, "123456").status_code == 403
        outbox.settle(0)
