import time

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, unique_int


class TestDiscussionIntegration:
    @pytest.fixture
    def setup_discussion(
        self, user_client: UserCreator, api_client: TestClient
    ) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(
            api_client, creator.username, creator.password
        )
        suffix = unique_int(10000000, 99999999)
        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Disc Space ({suffix})",
                "intro": "Test",
                "description": "Desc",
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert space_resp.status_code == 201
        space_id = space_resp.json()["data"]["space"]["id"]
        category_id = space_resp.json()["data"]["space"]["defaultCategoryId"]
        deadline = int(time.time() * 1000) + 7 * 24 * 60 * 60 * 1000
        task_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Disc Task ({suffix})",
                "intro": "This is a test task",
                "description": '{"type":"doc","content":[]}',
                "submitterType": "USER",
                "deadline": deadline,
                "resubmittable": True,
                "editable": True,
                "space": space_id,
                "categoryId": category_id,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert task_resp.status_code == 200, (
            f"Task creation failed: {task_resp.json()}"
        )
        task_id = task_resp.json()["data"]["task"]["id"]
        return {
            "creator": creator,
            "space_id": space_id,
            "task_id": task_id,
        }

    def test_create_discussion(self, setup_discussion: dict, api_client: TestClient):
        creator = setup_discussion["creator"]
        task_id = setup_discussion["task_id"]
        resp = api_client.post(
            "/discussions",
            json={
                "modelType": "task",
                "modelId": task_id,
                "content": "This is a test discussion comment.",
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 201, (
            f"Expected 201, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()["data"]["discussion"]
        # content is stored as a string (plain text or JSON serialized string)
        assert "This is a test discussion comment." in data["content"]

    def test_create_nested_discussion(
        self, setup_discussion: dict, api_client: TestClient
    ):
        creator = setup_discussion["creator"]
        task_id = setup_discussion["task_id"]
        parent_resp = api_client.post(
            "/discussions",
            json={
                "modelType": "task",
                "modelId": task_id,
                "content": "Parent comment",
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert parent_resp.status_code == 201, (
            f"Expected 201, got {parent_resp.status_code}"
        )
        parent_id = parent_resp.json()["data"]["discussion"]["id"]
        reply_resp = api_client.post(
            "/discussions",
            json={
                "modelType": "task",
                "modelId": task_id,
                "content": "This is a reply",
                "parentId": parent_id,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert reply_resp.status_code == 201, (
            f"Expected 201, got {reply_resp.status_code}"
        )

    def test_list_discussions(self, setup_discussion: dict, api_client: TestClient):
        creator = setup_discussion["creator"]
        task_id = setup_discussion["task_id"]
        api_client.post(
            "/discussions",
            json={
                "modelType": "task",
                "modelId": task_id,
                "content": "Comment 1",
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        api_client.post(
            "/discussions",
            json={
                "modelType": "task",
                "modelId": task_id,
                "content": "Comment 2",
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        resp = api_client.get(
            "/discussions",
            params={"modelType": "task", "modelId": task_id},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        discussions = resp.json()["data"]["discussions"]
        assert len(discussions) >= 2

    def test_get_discussion_by_id(self, setup_discussion: dict, api_client: TestClient):
        creator = setup_discussion["creator"]
        task_id = setup_discussion["task_id"]
        create_resp = api_client.post(
            "/discussions",
            json={
                "modelType": "task",
                "modelId": task_id,
                "content": "Get by ID test",
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 201, (
            f"Create failed: {create_resp.status_code}"
        )
        discussion_id = create_resp.json()["data"]["discussion"]["id"]
        resp = api_client.get(
            f"/discussions/{discussion_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["discussion"]["id"] == discussion_id

    def test_delete_discussion(self, setup_discussion: dict, api_client: TestClient):
        creator = setup_discussion["creator"]
        task_id = setup_discussion["task_id"]
        create_resp = api_client.post(
            "/discussions",
            json={
                "modelType": "task",
                "modelId": task_id,
                "content": "To be deleted",
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 201, (
            f"Create failed: {create_resp.status_code}"
        )
        discussion_id = create_resp.json()["data"]["discussion"]["id"]
        resp = api_client.delete(
            f"/discussions/{discussion_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}"

    def test_toggle_reaction(self, setup_discussion: dict, api_client: TestClient):
        creator = setup_discussion["creator"]
        task_id = setup_discussion["task_id"]
        create_resp = api_client.post(
            "/discussions",
            json={
                "modelType": "task",
                "modelId": task_id,
                "content": "Reaction test",
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 201
        discussion_id = create_resp.json()["data"]["discussion"]["id"]
        resp = api_client.post(
            f"/discussions/{discussion_id}/reactions/1",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Toggle reaction failed: {resp.text}"
        data = resp.json()["data"]
        # NT DiscussionReactionSummary: {reaction: {reactionType, count, hasReacted}}
        assert "reaction" in data
        assert data["reaction"]["hasReacted"] is True

    def test_remove_reaction(self, setup_discussion: dict, api_client: TestClient):
        creator = setup_discussion["creator"]
        task_id = setup_discussion["task_id"]
        create_resp = api_client.post(
            "/discussions",
            json={
                "modelType": "task",
                "modelId": task_id,
                "content": "Remove reaction test",
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 201
        discussion_id = create_resp.json()["data"]["discussion"]["id"]
        api_client.post(
            f"/discussions/{discussion_id}/reactions/1",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        resp = api_client.delete(
            f"/discussions/{discussion_id}/reactions/1",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Remove reaction failed: {resp.text}"
        data = resp.json()["data"]
        # NT DiscussionReactionSummary: {reaction: {reactionType, count, hasReacted}}
        assert "reaction" in data
        assert data["reaction"]["hasReacted"] is False
