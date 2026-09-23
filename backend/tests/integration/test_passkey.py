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
