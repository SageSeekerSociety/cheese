import random
import time

import httpx
import pytest

from tests.integration.conftest import UserCreator


class TestProjectIntegration:
    @pytest.fixture
    def setup_project(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)
        suffix = random.randint(10000000, 99999999)
        team_resp = api_client.post(
            "/teams",
            json={
                "name": f"Project Team ({suffix})",
                "intro": "Test team for project",
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

    def test_create_project(self, setup_project: dict, api_client: httpx.Client):
        creator = setup_project["creator"]
        team_id = setup_project["team_id"]
        now = int(time.time() * 1000)
        resp = api_client.post(
            "/projects",
            json={
                "name": "Test Project",
                "description": "Test Description",
                "colorCode": "#FFFFFF",
                "startDate": now,
                "endDate": now + 86400000,
                "teamId": team_id,
                "leaderId": creator.user_id,
                "content": "Test Content",
                "parentId": None,
                "externalTaskId": None,
                "githubRepo": None,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]["project"]
        assert data["name"] == "Test Project"

    def test_get_projects(self, setup_project: dict, api_client: httpx.Client):
        creator = setup_project["creator"]
        team_id = setup_project["team_id"]
        now = int(time.time() * 1000)
        api_client.post(
            "/projects",
            json={
                "name": "List Test Project",
                "description": "Test",
                "colorCode": "#000000",
                "startDate": now,
                "endDate": now + 86400000,
                "teamId": team_id,
                "leaderId": creator.user_id,
                "content": "Content",
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        resp = api_client.get(
            "/projects",
            params={"team_id": team_id},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        projects = resp.json()["data"]["projects"]
        assert isinstance(projects, list)

    def test_update_project(self, setup_project: dict, api_client: httpx.Client):
        creator = setup_project["creator"]
        team_id = setup_project["team_id"]
        now = int(time.time() * 1000)

        create_resp = api_client.post(
            "/projects",
            json={
                "name": "Original Project",
                "description": "Original Description",
                "colorCode": "#FFFFFF",
                "startDate": now,
                "endDate": now + 86400000,
                "teamId": team_id,
                "leaderId": creator.user_id,
                "content": "Original Content",
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 201
        project_id = create_resp.json()["data"]["project"]["id"]

        resp = api_client.patch(
            f"/projects/{project_id}",
            json={
                "name": "Updated Project",
                "description": "Updated Description",
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]["project"]
        assert data["name"] == "Updated Project"
        assert data["description"] == "Updated Description"

    def test_delete_project(self, setup_project: dict, api_client: httpx.Client):
        creator = setup_project["creator"]
        team_id = setup_project["team_id"]
        now = int(time.time() * 1000)

        create_resp = api_client.post(
            "/projects",
            json={
                "name": "Project to Delete",
                "description": "Will be deleted",
                "colorCode": "#FF0000",
                "startDate": now,
                "endDate": now + 86400000,
                "teamId": team_id,
                "leaderId": creator.user_id,
                "content": "Delete me",
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 201
        project_id = create_resp.json()["data"]["project"]["id"]

        resp = api_client.delete(
            f"/projects/{project_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"
