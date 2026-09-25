import base64
import hashlib
import json
import os
import struct

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import CreatedUser, UserCreator


def _fake_credential(challenge_b64url: str = "bm90LWEtcmVhbC1jaGFsbGVuZ2U") -> dict:
    """A structurally valid WebAuthn credential wrapper whose clientDataJSON
    echoes the given (base64url) challenge — enough to exercise the server's
    challenge extraction + lookup without real authenticator crypto."""
    client_data = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"challenge": challenge_b64url, "type": "webauthn.create"}
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    return {
        "id": "test",
        "type": "public-key",
        "response": {"clientDataJSON": client_data},
    }


def _sudo_ticket(api_client: TestClient, user: CreatedUser, purpose: str) -> str:
    """A ticket got the way a client gets one: by re-entering the password."""
    resp = api_client.post(
        "/users/auth/sudo",
        headers={"Authorization": f"Bearer {user.token}"},
        json={
            "method": "password",
            "credentials": {"password": user.password},
            "purpose": purpose,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["sudoTicket"]


class TestPasskeyIntegration:
    def test_register_options(
        self, authenticated_user: CreatedUser, api_client: TestClient
    ):
        resp = api_client.post(
            f"/users/{authenticated_user.user_id}/passkeys/options",
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
            json={
                "sudoTicket": _sudo_ticket(
                    api_client, authenticated_user, "passkey:add"
                )
            },
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()["data"]
        assert "options" in data
        options = data["options"]
        assert "challenge" in options
        assert "rp" in options
        assert "user" in options
        assert options["rp"]["id"] is not None
        assert options["user"]["name"] is not None

    def test_authenticate_challenge(self, api_client: TestClient):
        resp = api_client.post(
            "/users/auth/passkey/options",
            json={},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()["data"]
        assert "options" in data
        options = data["options"]
        assert "challenge" in options
        assert "rpId" in options
        assert "timeout" in options

    def test_authenticate_challenge_with_user_id(
        self, authenticated_user: CreatedUser, api_client: TestClient
    ):
        resp = api_client.post(
            "/users/auth/passkey/options",
            json={"userId": authenticated_user.user_id},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()["data"]
        assert "options" in data

    def test_list_passkeys_empty(
        self, authenticated_user: CreatedUser, api_client: TestClient
    ):
        resp = api_client.get(
            f"/users/{authenticated_user.user_id}/passkeys",
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()["data"]
        assert "passkeys" in data
        assert isinstance(data["passkeys"], list)

    def test_list_passkeys_forbidden_for_other_user(
        self, user_client: UserCreator, api_client: TestClient
    ):
        user1 = user_client.create_user()
        user1.token = user_client.login(api_client, user1.username, user1.password)
        user2 = user_client.create_user()
        resp = api_client.get(
            f"/users/{user2.user_id}/passkeys",
            headers={"Authorization": f"Bearer {user1.token}"},
        )
        assert resp.status_code == 403, (
            f"Expected 403, got {resp.status_code}: {resp.text}"
        )

    def test_delete_passkey_not_found(
        self, authenticated_user: CreatedUser, api_client: TestClient
    ):
        resp = api_client.request(
            "DELETE",
            f"/users/{authenticated_user.user_id}/passkeys/nonexistent-credential-id",
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
            json={
                "sudoTicket": _sudo_ticket(
                    api_client, authenticated_user, "passkey:delete"
                )
            },
        )
        assert resp.status_code == 404, (
            f"Expected 404, got {resp.status_code}: {resp.text}"
        )

    def test_register_verify_unknown_challenge(
        self, authenticated_user: CreatedUser, api_client: TestClient
    ):
        resp = api_client.post(
            f"/users/{authenticated_user.user_id}/passkeys",
            json={"response": _fake_credential()},
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
        )
        assert resp.status_code == 400, (
            f"Expected 400, got {resp.status_code}: {resp.text}"
        )

    def test_register_verify_malformed_credential(
        self, authenticated_user: CreatedUser, api_client: TestClient
    ):
        resp = api_client.post(
            f"/users/{authenticated_user.user_id}/passkeys",
            json={"response": {"id": "test", "type": "public-key"}},
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
        )
        assert resp.status_code == 400, (
            f"Expected 400, got {resp.status_code}: {resp.text}"
        )

    def test_authenticate_verify_unknown_challenge(self, api_client: TestClient):
        resp = api_client.post(
            "/users/auth/passkey/verify",
            json={"response": _fake_credential()},
        )
        assert resp.status_code == 400, (
            f"Expected 400, got {resp.status_code}: {resp.text}"
        )


class TestPasskeyManagementNeedsReAuthentication:
    """Adding or removing a passkey changes how the account can be signed
    into, so a live session alone is not enough for either."""

    def _options(self, api_client: TestClient, user: CreatedUser, ticket=None):
        return api_client.post(
            f"/users/{user.user_id}/passkeys/options",
            headers={"Authorization": f"Bearer {user.token}"},
            json={} if ticket is None else {"sudoTicket": ticket},
        )

    def _delete(self, api_client: TestClient, user: CreatedUser, ticket=None):
        return api_client.request(
            "DELETE",
            f"/users/{user.user_id}/passkeys/some-credential-id",
            headers={"Authorization": f"Bearer {user.token}"},
            json={} if ticket is None else {"sudoTicket": ticket},
        )

    def test_starting_a_registration_needs_a_ticket(
        self, authenticated_user: CreatedUser, api_client: TestClient
    ):
        refused = self._options(api_client, authenticated_user)
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["name"] == "SudoRequiredError"

    def test_deleting_a_passkey_needs_a_ticket(
        self, authenticated_user: CreatedUser, api_client: TestClient
    ):
        refused = self._delete(api_client, authenticated_user)
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["name"] == "SudoRequiredError"

    def test_a_ticket_for_one_is_not_a_ticket_for_the_other(
        self, authenticated_user: CreatedUser, api_client: TestClient
    ):
        add = _sudo_ticket(api_client, authenticated_user, "passkey:add")
        refused = self._delete(api_client, authenticated_user, add)
        assert refused.status_code == 403, refused.text

        delete = _sudo_ticket(api_client, authenticated_user, "passkey:delete")
        refused = self._options(api_client, authenticated_user, delete)
        assert refused.status_code == 403, refused.text

    def test_a_registration_ticket_is_spent_by_its_first_use(
        self, authenticated_user: CreatedUser, api_client: TestClient
    ):
        ticket = _sudo_ticket(api_client, authenticated_user, "passkey:add")
        assert self._options(api_client, authenticated_user, ticket).status_code == 200

        replay = self._options(api_client, authenticated_user, ticket)
        assert replay.status_code == 403, replay.text


_USER_PRESENT = 0x01
_USER_VERIFIED = 0x04
_ATTESTED_CREDENTIAL = 0x40


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class _SoftAuthenticator:
    """A WebAuthn authenticator in software: a P-256 key that registers with
    "none" attestation and signs assertions, with or without the flag that
    says it verified its user (a PIN or biometric, as opposed to a touch)."""

    def __init__(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import ec

        from app.core.config import settings

        self._key = ec.generate_private_key(ec.SECP256R1())
        self._credential_id = os.urandom(16)
        self._rp_id_hash = hashlib.sha256(settings.webauthn_rp_id.encode()).digest()
        self._origin = settings.webauthn_origin
        self._sign_count = 0

    def _client_data(self, kind: str, challenge: str) -> bytes:
        return json.dumps(
            {"type": kind, "challenge": challenge, "origin": self._origin}
        ).encode()

    def _public_key(self) -> bytes:
        import cbor2

        numbers = self._key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    def register(self, challenge: str) -> dict:
        import cbor2

        auth_data = (
            self._rp_id_hash
            + bytes([_USER_PRESENT | _ATTESTED_CREDENTIAL])
            + struct.pack(">I", 0)
            + bytes(16)
            + struct.pack(">H", len(self._credential_id))
            + self._credential_id
            + self._public_key()
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": _b64url(self._credential_id),
            "rawId": _b64url(self._credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": _b64url(
                    self._client_data("webauthn.create", challenge)
                ),
                "attestationObject": _b64url(attestation),
                "transports": ["usb"],
            },
        }

    def assertion(self, challenge: str, *, verified_user: bool) -> dict:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        self._sign_count += 1
        flags = _USER_PRESENT | (_USER_VERIFIED if verified_user else 0)
        auth_data = (
            self._rp_id_hash + bytes([flags]) + struct.pack(">I", self._sign_count)
        )
        client_data = self._client_data("webauthn.get", challenge)
        signature = self._key.sign(
            auth_data + hashlib.sha256(client_data).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
        return {
            "id": _b64url(self._credential_id),
            "rawId": _b64url(self._credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": _b64url(client_data),
                "authenticatorData": _b64url(auth_data),
                "signature": _b64url(signature),
            },
        }


class TestPasskeySignInVerifiesTheUser:
    """A passkey sign-in is complete on its own, with no second factor after
    it, so the key must have verified its user. A security key that was only
    touched proves possession alone."""

    @pytest.fixture
    def key(
        self, authenticated_user: CreatedUser, api_client: TestClient
    ) -> _SoftAuthenticator:
        # Registered without user verification, as a key without a PIN does.
        authenticator = _SoftAuthenticator()
        user = authenticated_user
        headers = {"Authorization": f"Bearer {user.token}"}
        options = api_client.post(
            f"/users/{user.user_id}/passkeys/options",
            headers=headers,
            json={"sudoTicket": _sudo_ticket(api_client, user, "passkey:add")},
        )
        assert options.status_code == 200, options.text
        challenge = options.json()["data"]["options"]["challenge"]
        registered = api_client.post(
            f"/users/{user.user_id}/passkeys",
            headers=headers,
            json={"response": authenticator.register(challenge)},
        )
        assert registered.status_code == 200, registered.text
        return authenticator

    def _challenge(self, api_client: TestClient) -> str:
        resp = api_client.post("/users/auth/passkey/options", json={})
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]["options"]["challenge"]

    def _sign_in(self, api_client: TestClient, assertion: dict):
        return api_client.post(
            "/users/auth/passkey/verify", json={"response": assertion}
        )

    def _sudo(self, api_client: TestClient, user: CreatedUser, assertion: dict):
        return api_client.post(
            "/users/auth/sudo",
            headers={"Authorization": f"Bearer {user.token}"},
            json={"method": "passkey", "credentials": {"passkeyResponse": assertion}},
        )

    def test_a_verified_assertion_signs_in(
        self,
        key: _SoftAuthenticator,
        authenticated_user: CreatedUser,
        api_client: TestClient,
    ):
        challenge = self._challenge(api_client)

        resp = self._sign_in(api_client, key.assertion(challenge, verified_user=True))

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["accessToken"]

    def test_an_assertion_without_user_verification_does_not_sign_in(
        self, key: _SoftAuthenticator, api_client: TestClient
    ):
        challenge = self._challenge(api_client)

        resp = self._sign_in(api_client, key.assertion(challenge, verified_user=False))

        assert resp.status_code == 400, resp.text
        assert "accessToken" not in resp.text

    def test_an_assertion_without_user_verification_does_not_grant_sudo(
        self,
        key: _SoftAuthenticator,
        authenticated_user: CreatedUser,
        api_client: TestClient,
    ):
        unverified = key.assertion(self._challenge(api_client), verified_user=False)
        refused = self._sudo(api_client, authenticated_user, unverified)
        assert refused.status_code == 400, refused.text

        verified = key.assertion(self._challenge(api_client), verified_user=True)
        granted = self._sudo(api_client, authenticated_user, verified)
        assert granted.status_code == 200, granted.text

    def test_sign_in_options_ask_the_key_to_verify_its_user(
        self, api_client: TestClient
    ):
        resp = api_client.post("/users/auth/passkey/options", json={})

        assert resp.json()["data"]["options"]["userVerification"] == "required"
