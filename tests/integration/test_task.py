import os
import random
from datetime import datetime, timezone

import httpx
import pytest

from tests.integration.conftest import UserCreator

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("RUN_INTEGRATION_TESTS", "").lower() not in ("1", "true"),
        reason="Integration tests require RUN_INTEGRATION_TESTS=1 and a running database",
    ),
]


class TestTaskIntegration:
    @pytest.fixture
    def task_setup(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        participant = user_client.create_user()
        participant.token = user_client.login(
            api_client, participant.username, participant.password
        )

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Test Space ({suffix})",
                "intro": "A space for testing",
                "description": "Test description",
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert space_resp.status_code in (200, 201), f"Failed to create space: {space_resp.text}"
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        category_id = space_data.get("defaultCategoryId")

        deadline_ms = int(
            (datetime.now(timezone.utc).timestamp() + 7 * 24 * 3600) * 1000
        )

        return {
            "creator": creator,
            "participant": participant,
            "space_id": space_id,
            "category_id": category_id,
            "task_name": f"Test Task ({suffix})",
            "task_intro": "This is a test task",
            "task_description": '{"type":"doc","content":[]}',
            "deadline_ms": deadline_ms,
        }

    def test_create_task(self, api_client: httpx.Client, task_setup: dict) -> None:
        creator = task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        response = api_client.post(
            "/tasks",
            json={
                "name": task_setup["task_name"],
                "intro": task_setup["task_intro"],
                "description": task_setup["task_description"],
                "space": task_setup["space_id"],
                "categoryId": task_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
                "deadline": task_setup["deadline_ms"],
            },
            headers=headers,
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert data["data"]["task"]["name"] == task_setup["task_name"]
        assert data["data"]["task"]["id"] > 0

    def test_get_task(self, api_client: httpx.Client, task_setup: dict) -> None:
        creator = task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": task_setup["task_name"],
                "intro": task_setup["task_intro"],
                "description": task_setup["task_description"],
                "space": task_setup["space_id"],
                "categoryId": task_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers=headers,
        )
        assert create_resp.status_code == 200
        task_id = create_resp.json()["data"]["task"]["id"]

        get_resp = api_client.get(f"/tasks/{task_id}", headers=headers)
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["data"]["task"]["id"] == task_id
        assert data["data"]["task"]["name"] == task_setup["task_name"]

    def test_update_task(self, api_client: httpx.Client, task_setup: dict) -> None:
        creator = task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": task_setup["task_name"],
                "intro": task_setup["task_intro"],
                "description": task_setup["task_description"],
                "space": task_setup["space_id"],
                "categoryId": task_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers=headers,
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        updated_name = f"{task_setup['task_name']} (Updated)"
        patch_resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"name": updated_name},
            headers=headers,
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["data"]["task"]["name"] == updated_name

    def test_enumerate_tasks(self, api_client: httpx.Client, task_setup: dict) -> None:
        creator = task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": task_setup["task_name"],
                "intro": task_setup["task_intro"],
                "description": task_setup["task_description"],
                "space": task_setup["space_id"],
                "categoryId": task_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers=headers,
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        list_resp = api_client.get(
            "/tasks",
            params={"spaceId": task_setup["space_id"]},
            headers=headers,
        )
        assert list_resp.status_code == 200
        task_ids = [t["id"] for t in list_resp.json()["data"]["tasks"]]
        assert task_id in task_ids

    def test_approve_task(self, api_client: httpx.Client, task_setup: dict) -> None:
        creator = task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": task_setup["task_name"],
                "intro": task_setup["task_intro"],
                "description": task_setup["task_description"],
                "space": task_setup["space_id"],
                "categoryId": task_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers=headers,
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        approve_resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers=headers,
        )
        assert approve_resp.status_code == 200
        assert approve_resp.json()["data"]["task"].get("approved") == "APPROVED"

    def test_join_task(self, api_client: httpx.Client, task_setup: dict) -> None:
        creator = task_setup["creator"]
        participant = task_setup["participant"]

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": task_setup["task_name"],
                "intro": task_setup["task_intro"],
                "description": task_setup["task_description"],
                "space": task_setup["space_id"],
                "categoryId": task_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        join_resp = api_client.post(
            f"/tasks/{task_id}/participants",
            json={},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert join_resp.status_code in (200, 201), f"Join failed: {join_resp.text}"

        participants_resp = api_client.get(
            f"/tasks/{task_id}/participants",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert participants_resp.status_code == 200
        member_ids = [
            p.get("memberId") for p in participants_resp.json()["data"]["participants"]
        ]
        assert participant.user_id in member_ids

    def test_delete_task(self, api_client: httpx.Client, task_setup: dict) -> None:
        creator = task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": task_setup["task_name"],
                "intro": task_setup["task_intro"],
                "description": task_setup["task_description"],
                "space": task_setup["space_id"],
                "categoryId": task_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers=headers,
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        delete_resp = api_client.delete(f"/tasks/{task_id}", headers=headers)
        assert delete_resp.status_code == 204

        get_resp = api_client.get(f"/tasks/{task_id}", headers=headers)
        assert get_resp.status_code == 404

    def test_get_participants(self, api_client: httpx.Client, task_setup: dict) -> None:
        creator = task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": task_setup["task_name"],
                "intro": task_setup["task_intro"],
                "description": task_setup["task_description"],
                "space": task_setup["space_id"],
                "categoryId": task_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers=headers,
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        participants_resp = api_client.get(
            f"/tasks/{task_id}/participants", headers=headers
        )
        assert participants_resp.status_code == 200
        data = participants_resp.json()
        assert "participants" in data["data"]
