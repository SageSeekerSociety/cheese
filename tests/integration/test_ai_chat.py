import httpx

from tests.integration.conftest import CreatedUser


class TestAIChatIntegration:
    def test_list_models(self, authenticated_user: CreatedUser, api_client: httpx.Client):
        resp = api_client.get(
            "/ai/models",
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "models" in data
        assert len(data["models"]) > 0
        model = data["models"][0]
        assert "id" in model
        assert "name" in model

    def test_list_conversations_empty(
        self, authenticated_user: CreatedUser, api_client: httpx.Client
    ):
        resp = api_client.get(
            "/ai/conversations",
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "conversations" in data
        assert isinstance(data["conversations"], list)

    def test_create_conversation(self, authenticated_user: CreatedUser, api_client: httpx.Client):
        resp = api_client.post(
            "/ai/conversations",
            json={"title": "Test Conversation", "modelId": "gpt-4o-mini"},
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "conversation" in data
        conv = data["conversation"]
        assert conv["title"] == "Test Conversation"
        assert "id" in conv

    def test_get_conversation(self, authenticated_user: CreatedUser, api_client: httpx.Client):
        create_resp = api_client.post(
            "/ai/conversations",
            json={"title": "Get Test"},
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
        )
        assert create_resp.status_code == 201
        conv_id = create_resp.json()["data"]["conversation"]["id"]

        resp = api_client.get(
            f"/ai/conversations/{conv_id}",
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert data["conversation"]["id"] == conv_id
        assert "messages" in data["conversation"]

    def test_delete_conversation(self, authenticated_user: CreatedUser, api_client: httpx.Client):
        create_resp = api_client.post(
            "/ai/conversations",
            json={"title": "Delete Test"},
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
        )
        assert create_resp.status_code == 201
        conv_id = create_resp.json()["data"]["conversation"]["id"]

        resp = api_client.delete(
            f"/ai/conversations/{conv_id}",
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
        )
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"

        get_resp = api_client.get(
            f"/ai/conversations/{conv_id}",
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
        )
        assert get_resp.status_code == 404

    def test_update_conversation_title(
        self, authenticated_user: CreatedUser, api_client: httpx.Client
    ):
        create_resp = api_client.post(
            "/ai/conversations",
            json={"title": "Original Title"},
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
        )
        assert create_resp.status_code == 201
        conv_id = create_resp.json()["data"]["conversation"]["id"]

        resp = api_client.patch(
            f"/ai/conversations/{conv_id}",
            json={"title": "Updated Title"},
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert data["conversation"]["title"] == "Updated Title"

    def test_get_quota(self, authenticated_user: CreatedUser, api_client: httpx.Client):
        resp = api_client.get(
            "/ai/quota",
            headers={"Authorization": f"Bearer {authenticated_user.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "quota" in data
        quota = data["quota"]
        assert "daily" in quota or "remaining" in quota or "used" in quota
