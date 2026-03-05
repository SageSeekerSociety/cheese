import httpx

from tests.integration.conftest import CreatedUser, UserCreator


class TestPasskeyIntegration:
    def test_register_challenge(self, authenticated_user: CreatedUser, api_client: httpx.Client):
        resp = api_client.post(
            "/users/auth/passkey/register/challenge",
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "options" in data
        options = data["options"]
        assert "challenge" in options
        assert "rp" in options
        assert "user" in options
        assert options["rp"]["id"] is not None
        assert options["user"]["name"] is not None

    def test_authenticate_challenge(self, api_client: httpx.Client):
        resp = api_client.post(
            "/users/auth/passkey/authenticate/challenge",
            json={},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "options" in data
        options = data["options"]
        assert "challenge" in options
        assert "rpId" in options
        assert "timeout" in options

    def test_authenticate_challenge_with_user_id(
        self, authenticated_user: CreatedUser, api_client: httpx.Client
    ):
        resp = api_client.post(
            "/users/auth/passkey/authenticate/challenge",
            json={"userId": authenticated_user.user_id},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "options" in data

    def test_list_passkeys_empty(self, authenticated_user: CreatedUser, api_client: httpx.Client):
        resp = api_client.get(
            f"/users/{authenticated_user.user_id}/passkeys",
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "passkeys" in data
        assert isinstance(data["passkeys"], list)

    def test_list_passkeys_forbidden_for_other_user(
        self, user_client: UserCreator, api_client: httpx.Client
    ):
        user1 = user_client.create_user()
        user1.token = user_client.login(api_client, user1.username, user1.password)
        user2 = user_client.create_user()
        resp = api_client.get(
            f"/users/{user2.user_id}/passkeys",
            headers={"Authorization": f"Bearer {user1.token}"},
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"

    def test_delete_passkey_not_found(
        self, authenticated_user: CreatedUser, api_client: httpx.Client
    ):
        resp = api_client.delete(
            f"/users/{authenticated_user.user_id}/passkeys/nonexistent-credential-id",
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"

    def test_register_verify_invalid_challenge(
        self, authenticated_user: CreatedUser, api_client: httpx.Client
    ):
        resp = api_client.post(
            "/users/auth/passkey/register/verify",
            json={
                "challenge": "invalid-challenge",
                "credential": {"id": "test", "type": "public-key"},
            },
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

    def test_authenticate_verify_invalid_challenge(self, api_client: httpx.Client):
        resp = api_client.post(
            "/users/auth/passkey/authenticate/verify",
            json={
                "challenge": "invalid-challenge",
                "credential": {"id": "test", "type": "public-key"},
            },
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
