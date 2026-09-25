"""The ways the signed-in account can confirm its identity, as the identity
confirmation reads them to decide what to offer."""

import uuid
from datetime import UTC, datetime

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.domain.passkey.models import PasskeyCredential
from app.domain.user.models import User
from tests.integration.conftest import CreatedUser, UserCreator

METHODS = "/users/me/auth-methods"


def _methods(client: TestClient, user: CreatedUser) -> dict:
    resp = client.get(METHODS, headers={"Authorization": f"Bearer {user.token}"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _signed_in(client: TestClient, user_client: UserCreator) -> CreatedUser:
    user = user_client.create_user()
    user.token = user_client.login(client, user.username, user.password)
    return user


def test_it_needs_a_signed_in_caller(api_client: TestClient):
    assert api_client.get(METHODS).status_code == 401


def test_an_account_with_only_a_password_says_so(
    user_client: UserCreator, api_client: TestClient
):
    user = _signed_in(api_client, user_client)

    methods = _methods(api_client, user)

    assert methods["password"] is True
    assert methods["passkey"] is False
    assert methods["twoFactor"] is False


def test_an_account_without_a_password_says_so(
    db_session, _portal, user_client: UserCreator, api_client: TestClient
):
    user = _signed_in(api_client, user_client)

    async def drop_password() -> None:
        await db_session.execute(
            update(User).where(User.id == user.user_id).values(hashed_password=None)
        )
        await db_session.flush()

    _portal.call(drop_password)

    assert _methods(api_client, user)["password"] is False


def test_a_passkey_and_two_step_verification_are_reported(
    db_session, _portal, user_client: UserCreator, api_client: TestClient
):
    user = _signed_in(api_client, user_client)
    headers = {"Authorization": f"Bearer {user.token}"}

    async def add_passkey() -> None:
        now = datetime.now(UTC)
        db_session.add(
            PasskeyCredential(
                user_id=user.user_id,
                credential_id=uuid.uuid4().hex,
                public_key=b"key",
                device_type="singleDevice",
                created_at=now,
                updated_at=now,
            )
        )
        await db_session.flush()

    _portal.call(add_passkey)
    sudo = api_client.post(
        "/users/auth/sudo",
        headers=headers,
        json={
            "method": "password",
            "credentials": {"password": user.password},
            "purpose": "2fa:enable",
        },
    )
    init = api_client.post(
        f"/users/{user.user_id}/2fa/enable",
        headers=headers,
        json={"sudoTicket": sudo.json()["data"]["sudoTicket"]},
    )
    secret = init.json()["data"]["secret"]
    api_client.post(
        f"/users/{user.user_id}/2fa/enable",
        headers=headers,
        json={"secret": secret, "code": pyotp.TOTP(secret).now()},
    )

    methods = _methods(api_client, user)

    assert methods["passkey"] is True
    assert methods["twoFactor"] is True


def test_nobody_can_look_up_another_account_by_name(
    user_client: UserCreator, api_client: TestClient
):
    other = user_client.create_user()

    resp = api_client.get(f"/users/auth/methods/{other.username}")

    assert resp.status_code == 404
