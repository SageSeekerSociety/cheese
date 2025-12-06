from __future__ import annotations

import json
import random

import httpx
import pytest

from tests.integration.conftest import UserCreator


class TestKnowledgeIntegration:
    @pytest.fixture
    def setup_knowledge(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)
        suffix = random.randint(10000000, 99999999)
        team_resp = api_client.post(
            "/teams",
            json={
                "name": f"Knowledge Team ({suffix})",
                "intro": "Test team for knowledge",
                "description": "A lengthy text. " * 100,
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert team_resp.status_code == 201, f"Team creation failed: {team_resp.text}"
        team_id = team_resp.json()["data"]["team"]["id"]
        return {
            "creator": creator,
            "team_id": team_id,
        }

    def test_create_knowledge(self, setup_knowledge: dict, api_client: httpx.Client):
        creator = setup_knowledge["creator"]
        team_id = setup_knowledge["team_id"]
        knowledge_name = f"Test Knowledge {random.randint(100000, 999999)}"
        content_json = json.dumps({"text": "This is test knowledge content."})
        resp = api_client.post(
            "/knowledge",
            json={
                "name": knowledge_name,
                "description": "A test knowledge description.",
                "type": "TEXT",
                "content": {"text": "This is test knowledge content."},
                "teamId": team_id,
                "projectId": None,
                "discussionId": None,
                "labels": ["test", "demo"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]["knowledge"]
        assert data["name"] == knowledge_name
        assert data["teamId"] == team_id
        assert "test" in data["labels"]

    def test_get_knowledge_success(self, setup_knowledge: dict, api_client: httpx.Client):
        creator = setup_knowledge["creator"]
        team_id = setup_knowledge["team_id"]
        create_resp = api_client.post(
            "/knowledge",
            json={
                "name": f"Get Test Knowledge {random.randint(100000, 999999)}",
                "description": "Test",
                "type": "TEXT",
                "content": {"text": "Test content"},
                "teamId": team_id,
                "labels": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 200, f"Create failed: {create_resp.text}"
        knowledge_id = create_resp.json()["data"]["knowledge"]["id"]

        resp = api_client.get(
            f"/knowledge/{knowledge_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]["knowledge"]
        assert data["id"] == knowledge_id
        assert data["teamId"] == team_id

    def test_get_knowledge_not_found(self, setup_knowledge: dict, api_client: httpx.Client):
        creator = setup_knowledge["creator"]
        resp = api_client.get(
            "/knowledge/99999999",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"

    def test_list_knowledge_by_team(self, setup_knowledge: dict, api_client: httpx.Client):
        creator = setup_knowledge["creator"]
        team_id = setup_knowledge["team_id"]
        api_client.post(
            "/knowledge",
            json={
                "name": f"List Test Knowledge {random.randint(100000, 999999)}",
                "description": "Test",
                "type": "TEXT",
                "content": {"text": "Test content"},
                "teamId": team_id,
                "labels": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        resp = api_client.get(
            "/knowledge",
            params={"teamId": team_id, "page": 0, "pageSize": 10},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "knowledges" in data
        assert isinstance(data["knowledges"], list)

    def test_update_knowledge_success(self, setup_knowledge: dict, api_client: httpx.Client):
        creator = setup_knowledge["creator"]
        team_id = setup_knowledge["team_id"]
        create_resp = api_client.post(
            "/knowledge",
            json={
                "name": f"Update Test Knowledge {random.randint(100000, 999999)}",
                "description": "Original description",
                "type": "TEXT",
                "content": {"text": "Original content"},
                "teamId": team_id,
                "labels": ["original"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 200, f"Create failed: {create_resp.text}"
        knowledge_id = create_resp.json()["data"]["knowledge"]["id"]

        updated_name = "Updated Knowledge Name"
        updated_description = "Updated description"
        updated_content = {"text": "Updated content via integration test"}
        resp = api_client.patch(
            f"/knowledge/{knowledge_id}",
            json={
                "name": updated_name,
                "description": updated_description,
                "content": updated_content,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]["knowledge"]
        assert data["id"] == knowledge_id
        assert data["name"] == updated_name
        assert data["description"] == updated_description
        assert data["content"] == updated_content
        assert data["teamId"] == team_id

    def test_delete_knowledge_success(self, setup_knowledge: dict, api_client: httpx.Client):
        creator = setup_knowledge["creator"]
        team_id = setup_knowledge["team_id"]
        create_resp = api_client.post(
            "/knowledge",
            json={
                "name": f"Delete Test Knowledge {random.randint(100000, 999999)}",
                "description": "To be deleted",
                "type": "TEXT",
                "content": {"text": "Delete me"},
                "teamId": team_id,
                "labels": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 200, f"Create failed: {create_resp.text}"
        knowledge_id = create_resp.json()["data"]["knowledge"]["id"]

        resp = api_client.delete(
            f"/knowledge/{knowledge_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        get_resp = api_client.get(
            f"/knowledge/{knowledge_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert get_resp.status_code == 404, f"Expected 404 after delete, got {get_resp.status_code}"

    def test_list_knowledge_with_labels(self, setup_knowledge: dict, api_client: httpx.Client):
        creator = setup_knowledge["creator"]
        team_id = setup_knowledge["team_id"]

        api_client.post(
            "/knowledge",
            json={
                "name": f"Label Test Knowledge {random.randint(100000, 999999)}",
                "description": "Has specific label",
                "type": "TEXT",
                "content": {"text": "Labeled content"},
                "teamId": team_id,
                "labels": ["specific-label"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.get(
            "/knowledge",
            params={"teamId": team_id, "label": "specific-label"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "knowledges" in data

    def test_update_knowledge_labels(self, setup_knowledge: dict, api_client: httpx.Client):
        creator = setup_knowledge["creator"]
        team_id = setup_knowledge["team_id"]

        create_resp = api_client.post(
            "/knowledge",
            json={
                "name": f"Label Update Knowledge {random.randint(100000, 999999)}",
                "description": "Original labels",
                "type": "TEXT",
                "content": {"text": "Content"},
                "teamId": team_id,
                "labels": ["old-label"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 200
        knowledge_id = create_resp.json()["data"]["knowledge"]["id"]

        resp = api_client.patch(
            f"/knowledge/{knowledge_id}",
            json={"labels": ["new-label-1", "new-label-2"]},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]["knowledge"]
        assert "new-label-1" in data["labels"]
        assert "new-label-2" in data["labels"]
