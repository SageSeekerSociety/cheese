from datetime import UTC, datetime

import pytest
from anyio.from_thread import BlockingPortal
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.tag.models import Tag
from tests.integration.conftest import UserCreator, unique_int


def create_topics_in_db(
    db_session: AsyncSession,
    portal: BlockingPortal,
    topic_names: list[str],
    created_by: int,
) -> list[int]:
    topics = [
        Tag(name=name, created_by_id=created_by, created_at=datetime.now(UTC))
        for name in topic_names
    ]

    async def _do() -> list[int]:
        for t in topics:
            db_session.add(t)
        await db_session.flush()
        return [t.id for t in topics]

    return portal.call(_do)


class TestTaskTopicIntegration:
    @pytest.fixture
    def setup_task_topics(
        self,
        user_client: UserCreator,
        api_client: TestClient,
        db_session: AsyncSession,
        _portal: BlockingPortal,
    ) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(
            api_client, creator.username, creator.password
        )
        suffix = unique_int(10000000, 99999999)

        topic_names = [f"Test Topic ({suffix}) ({i})" for i in range(1, 5)]
        topic_ids = create_topics_in_db(
            db_session, _portal, topic_names, creator.user_id
        )

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Task Topic Test Space ({suffix})",
                "intro": "Test space for task topics",
                "description": "A lengthy text. " * 100,
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert space_resp.status_code == 201, (
            f"Space creation failed: {space_resp.text}"
        )
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        default_category_id = space_data["defaultCategoryId"]

        return {
            "creator": creator,
            "space_id": space_id,
            "default_category_id": default_category_id,
            "topic_ids": topic_ids,
            "topic_names": topic_names,
        }

    def test_create_task_with_topics(
        self, setup_task_topics: dict, api_client: TestClient
    ):
        creator = setup_task_topics["creator"]
        space_id = setup_task_topics["space_id"]
        category_id = setup_task_topics["default_category_id"]
        topic_ids = setup_task_topics["topic_ids"]

        deadline = int((datetime.now(UTC).timestamp() + 7 * 24 * 3600) * 1000)
        task_name = f"Task with Topics {unique_int(100000, 999999)}"

        resp = api_client.post(
            "/tasks",
            json={
                "name": task_name,
                "submitterType": "USER",
                "deadline": deadline,
                "resubmittable": True,
                "editable": True,
                "intro": "Test task intro " * 5,
                "description": "Test task description " * 10,
                "submissionSchema": [],
                "space": space_id,
                "categoryId": category_id,
                "topics": [topic_ids[0], topic_ids[1]],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()["data"]["task"]
        assert data["name"] == task_name

    def test_get_task_with_topics(
        self, setup_task_topics: dict, api_client: TestClient
    ):
        creator = setup_task_topics["creator"]
        space_id = setup_task_topics["space_id"]
        category_id = setup_task_topics["default_category_id"]
        topic_ids = setup_task_topics["topic_ids"]

        deadline = int((datetime.now(UTC).timestamp() + 7 * 24 * 3600) * 1000)

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Task with Topics {unique_int(100000, 999999)}",
                "submitterType": "USER",
                "deadline": deadline,
                "resubmittable": True,
                "editable": True,
                "intro": "Test task intro " * 5,
                "description": "Test task description " * 10,
                "space": space_id,
                "categoryId": category_id,
                "topics": [topic_ids[0], topic_ids[1]],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 200, f"Create failed: {create_resp.text}"
        task_id = create_resp.json()["data"]["task"]["id"]

        resp = api_client.get(
            f"/tasks/{task_id}",
            params={"queryTopics": "true"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )

    def test_update_task_topics(self, setup_task_topics: dict, api_client: TestClient):
        creator = setup_task_topics["creator"]
        space_id = setup_task_topics["space_id"]
        category_id = setup_task_topics["default_category_id"]
        topic_ids = setup_task_topics["topic_ids"]

        deadline = int((datetime.now(UTC).timestamp() + 7 * 24 * 3600) * 1000)

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Task to Update Topics {unique_int(100000, 999999)}",
                "submitterType": "USER",
                "deadline": deadline,
                "resubmittable": True,
                "editable": True,
                "intro": "Test task intro " * 5,
                "description": "Test task description " * 10,
                "space": space_id,
                "categoryId": category_id,
                "topics": [topic_ids[0], topic_ids[1]],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 200, f"Create failed: {create_resp.text}"
        task_id = create_resp.json()["data"]["task"]["id"]

        patch_resp = api_client.patch(
            f"/tasks/{task_id}",
            json={
                "topics": [topic_ids[1], topic_ids[2]],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert patch_resp.status_code == 200, (
            f"Expected 200, got {patch_resp.status_code}: {patch_resp.text}"
        )

    def test_get_task_with_updated_topics(
        self, setup_task_topics: dict, api_client: TestClient
    ):
        creator = setup_task_topics["creator"]
        space_id = setup_task_topics["space_id"]
        category_id = setup_task_topics["default_category_id"]
        topic_ids = setup_task_topics["topic_ids"]
        deadline = int((datetime.now(UTC).timestamp() + 7 * 24 * 3600) * 1000)

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Task to Verify Topics {unique_int(100000, 999999)}",
                "submitterType": "USER",
                "deadline": deadline,
                "resubmittable": True,
                "editable": True,
                "intro": "Test task intro " * 5,
                "description": "Test task description " * 10,
                "space": space_id,
                "categoryId": category_id,
                "topics": [topic_ids[0], topic_ids[1]],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 200, f"Create failed: {create_resp.text}"
        task_id = create_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task_id}",
            json={"topics": [topic_ids[1], topic_ids[2]]},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.get(
            f"/tasks/{task_id}",
            params={"queryTopics": "true"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        task_data = resp.json()["data"]["task"]
        topics = task_data.get("topics", [])
        topic_id_set = {t["id"] for t in topics}
        assert topic_ids[1] in topic_id_set, "Topic 1 should be in the updated task"
        assert topic_ids[2] in topic_id_set, "Topic 2 should be in the updated task"
        assert topic_ids[0] not in topic_id_set, (
            "Topic 0 should not be in the updated task"
        )

    def test_enumerate_tasks_by_topics(
        self, setup_task_topics: dict, api_client: TestClient
    ):
        creator = setup_task_topics["creator"]
        space_id = setup_task_topics["space_id"]
        category_id = setup_task_topics["default_category_id"]
        topic_ids = setup_task_topics["topic_ids"]

        deadline = int((datetime.now(UTC).timestamp() + 7 * 24 * 3600) * 1000)

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Task for Topic Filter {unique_int(100000, 999999)}",
                "submitterType": "USER",
                "deadline": deadline,
                "resubmittable": True,
                "editable": True,
                "intro": "Test task intro " * 5,
                "description": "Test task description " * 10,
                "space": space_id,
                "categoryId": category_id,
                "topics": [topic_ids[1], topic_ids[2]],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 200, f"Create failed: {create_resp.text}"
        task_id = create_resp.json()["data"]["task"]["id"]

        approve_resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert approve_resp.status_code == 200, (
            f"Task approval failed: {approve_resp.status_code}: {approve_resp.text}"
        )

        resp = api_client.get(
            "/tasks",
            params={
                "space": space_id,
                "approved": "APPROVED",
                "topics": [topic_ids[1]],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        tasks = resp.json()["data"]["tasks"]
        assert any(t["id"] == task_id for t in tasks), (
            f"Task {task_id} not found in filtered results"
        )

        resp2 = api_client.get(
            "/tasks",
            params={
                "space": space_id,
                "approved": "APPROVED",
                "topics": [topic_ids[0]],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp2.status_code == 200, (
            f"Expected 200, got {resp2.status_code}: {resp2.text}"
        )
        tasks2 = resp2.json()["data"]["tasks"]
        assert not any(t["id"] == task_id for t in tasks2), (
            f"Task {task_id} should not be in results filtered by topic {topic_ids[0]}"
        )

    def test_clear_task_topics(self, setup_task_topics: dict, api_client: TestClient):
        creator = setup_task_topics["creator"]
        space_id = setup_task_topics["space_id"]
        category_id = setup_task_topics["default_category_id"]
        topic_ids = setup_task_topics["topic_ids"]

        deadline = int((datetime.now(UTC).timestamp() + 7 * 24 * 3600) * 1000)

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Task to Clear Topics {unique_int(100000, 999999)}",
                "submitterType": "USER",
                "deadline": deadline,
                "resubmittable": True,
                "editable": True,
                "intro": "Test task intro " * 5,
                "description": "Test task description " * 10,
                "space": space_id,
                "categoryId": category_id,
                "topics": [topic_ids[0], topic_ids[1]],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 200, f"Create failed: {create_resp.text}"
        task_id = create_resp.json()["data"]["task"]["id"]

        patch_resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"topics": []},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert patch_resp.status_code == 200, (
            f"Expected 200, got {patch_resp.status_code}: {patch_resp.text}"
        )
