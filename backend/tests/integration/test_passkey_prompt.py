"""The offer to add a passkey after signing in, and how declining it backs off.

What the sign-in response says is what the client acts on, so every
assertion reads ``passkeyEnrollment`` from a real sign-in.
"""

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta

import cbor2
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.core.config import settings
from tests.integration.conftest import CreatedUser, UserCreator


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _sign_in(api_client: TestClient, user: CreatedUser) -> dict:
    resp = api_client.post(
        "/users/auth/login",
        json={"username": user.username, "password": user.password},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    user.token = data["accessToken"]
    return data["passkeyEnrollment"]


def _decline(api_client: TestClient, user: CreatedUser, **body):
    resp = api_client.post(
        f"/users/{user.user_id}/passkeys/prompt/dismiss",
        headers={"Authorization": f"Bearer {user.token}"},
        json=body,
    )
    assert resp.status_code == 200, resp.text


def _create_passkey(api_client: TestClient, user: CreatedUser, ticket: str):
    """Register a passkey the way a password manager does when it creates one
    without asking: user present, user *not* verified."""
    headers = {"Authorization": f"Bearer {user.token}"}
    options = api_client.post(
        f"/users/{user.user_id}/passkeys/options",
        headers=headers,
        json={"sudoTicket": ticket},
    )
    assert options.status_code == 200, options.text
    challenge = options.json()["data"]["options"]["challenge"]

    key = ec.generate_private_key(ec.SECP256R1())
    numbers = key.public_key().public_numbers()
    cose_key = cbor2.dumps(
        {
            1: 2,
            3: -7,
            -1: 1,
            -2: numbers.x.to_bytes(32, "big"),
            -3: numbers.y.to_bytes(32, "big"),
        }
    )
    credential_id = hashlib.sha256(challenge.encode()).digest()
    user_present = 0x01
    attested_data = 0x40
    auth_data = (
        hashlib.sha256(settings.webauthn_rp_id.encode()).digest()
        + bytes([user_present | attested_data])
        + (0).to_bytes(4, "big")
        + bytes(16)
        + len(credential_id).to_bytes(2, "big")
        + credential_id
        + cose_key
    )
    client_data = json.dumps(
        {
            "type": "webauthn.create",
            "challenge": challenge,
            "origin": settings.webauthn_origin,
            "crossOrigin": False,
        }
    ).encode()
    return api_client.post(
        f"/users/{user.user_id}/passkeys",
        headers=headers,
        json={
            "response": {
                "id": _b64url(credential_id),
                "rawId": _b64url(credential_id),
                "type": "public-key",
                "response": {
                    "clientDataJSON": _b64url(client_data),
                    "attestationObject": _b64url(
                        cbor2.dumps(
                            {"fmt": "none", "attStmt": {}, "authData": auth_data}
                        )
                    ),
                },
            }
        },
    )


@pytest.fixture
def clock(monkeypatch):
    """Moves the offer's idea of "now" forward, as the days pass."""
    import app.domain.passkey.prompt as prompt

    start = datetime.now(UTC)

    class Clock:
        def advance(self, delta: timedelta) -> None:
            nonlocal start
            start = start + delta

    monkeypatch.setattr(prompt, "_utcnow", lambda: start)
    return Clock()


@pytest.fixture
def user(user_client: UserCreator) -> CreatedUser:
    return user_client.create_user()


class TestTheOffer:
    def test_an_account_without_a_passkey_is_offered_one(
        self, api_client: TestClient, user: CreatedUser
    ):
        enrollment = _sign_in(api_client, user)

        assert enrollment["offer"] is True
        assert enrollment["canStopAsking"] is False

    def test_a_passkey_made_without_user_verification_is_accepted(
        self, api_client: TestClient, user: CreatedUser
    ):
        enrollment = _sign_in(api_client, user)

        resp = _create_passkey(api_client, user, enrollment["ticket"])

        assert resp.status_code == 200, resp.text
        listed = api_client.get(
            f"/users/{user.user_id}/passkeys",
            headers={"Authorization": f"Bearer {user.token}"},
        )
        assert len(listed.json()["data"]["passkeys"]) == 1

    def test_an_account_with_a_passkey_is_not_offered_one(
        self, api_client: TestClient, user: CreatedUser
    ):
        _create_passkey(api_client, user, _sign_in(api_client, user)["ticket"])

        assert _sign_in(api_client, user)["offer"] is False

    def test_adding_a_passkey_ends_the_offer_even_once_it_is_removed(
        self, api_client: TestClient, user: CreatedUser
    ):
        created = _create_passkey(
            api_client, user, _sign_in(api_client, user)["ticket"]
        )
        credential_id = created.json()["data"]["passkey"]["credentialId"]
        sudo = api_client.post(
            "/users/auth/sudo",
            headers={"Authorization": f"Bearer {user.token}"},
            json={
                "method": "password",
                "credentials": {"password": user.password},
                "purpose": "passkey:delete",
            },
        )
        removed = api_client.request(
            "DELETE",
            f"/users/{user.user_id}/passkeys/{credential_id}",
            headers={"Authorization": f"Bearer {user.token}"},
            json={"sudoTicket": sudo.json()["data"]["sudoTicket"]},
        )
        assert removed.status_code == 200, removed.text

        assert _sign_in(api_client, user)["offer"] is False

    def test_nobody_can_decline_it_for_someone_else(
        self, api_client: TestClient, user_client: UserCreator, user: CreatedUser
    ):
        other = user_client.create_user()
        _sign_in(api_client, other)

        resp = api_client.post(
            f"/users/{user.user_id}/passkeys/prompt/dismiss",
            headers={"Authorization": f"Bearer {other.token}"},
            json={},
        )

        assert resp.status_code == 403, resp.text
        assert _sign_in(api_client, user)["offer"] is True


class TestDecliningBacksOff:
    def test_thirty_days_then_ninety_then_never(
        self, api_client: TestClient, user: CreatedUser, clock
    ):
        _sign_in(api_client, user)
        _decline(api_client, user)

        clock.advance(timedelta(days=29))
        assert _sign_in(api_client, user)["offer"] is False
        clock.advance(timedelta(days=2))
        second = _sign_in(api_client, user)
        assert second["offer"] is True
        assert second["canStopAsking"] is True

        _decline(api_client, user)
        clock.advance(timedelta(days=89))
        assert _sign_in(api_client, user)["offer"] is False
        clock.advance(timedelta(days=2))
        assert _sign_in(api_client, user)["offer"] is True

        _decline(api_client, user)
        clock.advance(timedelta(days=3650))
        assert _sign_in(api_client, user)["offer"] is False

    def test_do_not_ask_again_stops_it_for_good(
        self, api_client: TestClient, user: CreatedUser, clock
    ):
        _sign_in(api_client, user)
        _decline(api_client, user)
        clock.advance(timedelta(days=31))
        assert _sign_in(api_client, user)["offer"] is True

        _decline(api_client, user, forever=True)
        clock.advance(timedelta(days=3650))

        assert _sign_in(api_client, user)["offer"] is False

    def test_declining_the_same_showing_twice_counts_once(
        self, api_client: TestClient, user: CreatedUser, clock
    ):
        _sign_in(api_client, user)
        _decline(api_client, user)
        _decline(api_client, user)

        clock.advance(timedelta(days=31))

        assert _sign_in(api_client, user)["offer"] is True
