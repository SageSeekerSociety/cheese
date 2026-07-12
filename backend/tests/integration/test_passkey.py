import base64
import json

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


class TestPasskeyIntegration:
    def test_register_options(
        self, authenticated_user: CreatedUser, api_client: TestClient
    ):
        resp = api_client.post(
            f"/users/{authenticated_user.user_id}/passkeys/options",
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
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
        resp = api_client.delete(
            f"/users/{authenticated_user.user_id}/passkeys/nonexistent-credential-id",
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
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
