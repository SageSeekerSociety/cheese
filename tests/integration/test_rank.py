from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, unique_int


class TestRankIntegration:
    @pytest.fixture
    def setup_rank_test(self, user_client: UserCreator, api_client: TestClient) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        participant = user_client.create_user()
        participant.token = user_client.login(
            api_client, participant.username, participant.password
        )

        suffix = unique_int(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Rank Test Space ({suffix})",
                "intro": "Test space for ranks",
                "description": "Description of space",
                "avatarId": 1,
                "enableRank": False,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert space_resp.status_code == 201, f"Space creation failed: {space_resp.text}"
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        default_category_id = space_data["defaultCategoryId"]

        deadline = int((datetime.now(UTC).timestamp() + 7 * 24 * 3600) * 1000)

        task1_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Rank 1 Task ({suffix})",
                "submitterType": "USER",
                "deadline": deadline,
                "resubmittable": False,
                "editable": False,
                "intro": "Task intro " * 10,
                "description": "Task description " * 10,
                "submissionSchema": [{"prompt": "Text Entry", "type": "TEXT"}],
                "space": space_id,
                "categoryId": default_category_id,
                "rank": 1,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert task1_resp.status_code == 200, f"Task 1 creation failed: {task1_resp.text}"
        task1_id = task1_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task1_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        task2_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Rank 2 Task ({suffix})",
                "submitterType": "USER",
                "deadline": deadline,
                "resubmittable": False,
                "editable": False,
                "intro": "Task intro " * 10,
                "description": "Task description " * 10,
                "submissionSchema": [{"prompt": "Text Entry", "type": "TEXT"}],
                "space": space_id,
                "categoryId": default_category_id,
                "rank": 2,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert task2_resp.status_code == 200, f"Task 2 creation failed: {task2_resp.text}"
        task2_id = task2_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task2_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        join_resp = api_client.post(
            f"/tasks/{task1_id}/participants",
            params={"member": participant.user_id},
            json={},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert join_resp.status_code == 200, f"Join task 1 failed: {join_resp.text}"
        membership1_id = join_resp.json()["data"]["participant"]["id"]

        api_client.patch(
            f"/tasks/{task1_id}/participants/{membership1_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        submit_resp = api_client.post(
            f"/tasks/{task1_id}/participants/{membership1_id}/submissions",
            json=[{"text": "Test submission content"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert submit_resp.status_code == 200, f"Submit failed: {submit_resp.text}"
        submission1_id = submit_resp.json()["data"]["submission"]["id"]

        return {
            "creator": creator,
            "participant": participant,
            "space_id": space_id,
            "default_category_id": default_category_id,
            "task1_id": task1_id,
            "task2_id": task2_id,
            "membership1_id": membership1_id,
            "submission1_id": submission1_id,
        }

    def test_get_space_with_rank_disabled(self, setup_rank_test: dict, api_client: TestClient):
        participant = setup_rank_test["participant"]
        space_id = setup_rank_test["space_id"]

        resp = api_client.get(
            f"/spaces/{space_id}",
            params={"queryMyRank": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert data["space"]["id"] == space_id
        assert data["myRank"] == 0 or data.get("myRank") is None

    def test_enable_rank_for_space(self, setup_rank_test: dict, api_client: TestClient):
        creator = setup_rank_test["creator"]
        space_id = setup_rank_test["space_id"]

        resp = api_client.patch(
            f"/spaces/{space_id}",
            json={"enableRank": True},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]["space"]
        assert data["enableRank"] is True

    def test_get_space_with_rank_enabled(self, setup_rank_test: dict, api_client: TestClient):
        creator = setup_rank_test["creator"]
        participant = setup_rank_test["participant"]
        space_id = setup_rank_test["space_id"]

        api_client.patch(
            f"/spaces/{space_id}",
            json={"enableRank": True},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.get(
            f"/spaces/{space_id}",
            params={"queryMyRank": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert data["space"]["id"] == space_id
        assert data["myRank"] == 0

    def test_join_rank2_task_fails_with_rank0(
        self,
        setup_rank_test: dict,
        api_client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from app.core.config import settings as _settings

        # Rank enforcement is opt-in (APPLICATION_RANK_CHECK_ENFORCED=false by
        # default). The route used to enforce unconditionally — that's the
        # bug we just fixed — so this test now flips the flag explicitly.
        monkeypatch.setattr(_settings, "rank_check_enforced", True)

        creator = setup_rank_test["creator"]
        participant = setup_rank_test["participant"]
        space_id = setup_rank_test["space_id"]
        task2_id = setup_rank_test["task2_id"]

        api_client.patch(
            f"/spaces/{space_id}",
            json={"enableRank": True},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.post(
            f"/tasks/{task2_id}/participants",
            params={"member": participant.user_id},
            json={},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

    def test_review_submission_upgrades_rank(self, setup_rank_test: dict, api_client: TestClient):
        creator = setup_rank_test["creator"]
        participant = setup_rank_test["participant"]
        space_id = setup_rank_test["space_id"]
        task1_id = setup_rank_test["task1_id"]
        membership1_id = setup_rank_test["membership1_id"]
        submission1_id = setup_rank_test["submission1_id"]

        api_client.patch(
            f"/spaces/{space_id}",
            json={"enableRank": True},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.post(
            f"/tasks/{task1_id}/participants/{membership1_id}/submissions/{submission1_id}/review",
            json={"accepted": True, "score": 5, "comment": "Well done!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        space_resp = api_client.get(
            f"/spaces/{space_id}",
            params={"queryMyRank": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert space_resp.status_code == 200
        data = space_resp.json()["data"]
        assert data["myRank"] == 1

    def test_failing_review_does_not_upgrade_rank(
        self, setup_rank_test: dict, api_client: TestClient
    ):
        creator = setup_rank_test["creator"]
        participant = setup_rank_test["participant"]
        space_id = setup_rank_test["space_id"]
        task1_id = setup_rank_test["task1_id"]
        membership1_id = setup_rank_test["membership1_id"]
        submission1_id = setup_rank_test["submission1_id"]

        api_client.patch(
            f"/spaces/{space_id}",
            json={"enableRank": True},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.post(
            f"/tasks/{task1_id}/participants/{membership1_id}/submissions/{submission1_id}/review",
            json={"accepted": False, "score": 2, "comment": "Needs improvement"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        space_resp = api_client.get(
            f"/spaces/{space_id}",
            params={"queryMyRank": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert space_resp.status_code == 200
        data = space_resp.json()["data"]
        assert data["myRank"] == 0

    def test_update_review_to_accepted_upgrades_rank(
        self, setup_rank_test: dict, api_client: TestClient
    ):
        creator = setup_rank_test["creator"]
        participant = setup_rank_test["participant"]
        space_id = setup_rank_test["space_id"]
        task1_id = setup_rank_test["task1_id"]
        membership1_id = setup_rank_test["membership1_id"]
        submission1_id = setup_rank_test["submission1_id"]

        api_client.patch(
            f"/spaces/{space_id}",
            json={"enableRank": True},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        api_client.post(
            f"/tasks/{task1_id}/participants/{membership1_id}/submissions/{submission1_id}/review",
            json={"accepted": False, "score": 2, "comment": "Needs improvement"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        space_resp = api_client.get(
            f"/spaces/{space_id}",
            params={"queryMyRank": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        data = space_resp.json()["data"]
        assert data["myRank"] == 0

        resp = api_client.put(
            f"/tasks/{task1_id}/participants/{membership1_id}/submissions/{submission1_id}/review",
            json={"accepted": True, "score": 5, "comment": "Actually well done!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        space_resp2 = api_client.get(
            f"/spaces/{space_id}",
            params={"queryMyRank": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        data2 = space_resp2.json()["data"]
        assert data2["myRank"] == 1

    def test_join_rank2_task_succeeds_after_rank1_achieved(
        self, setup_rank_test: dict, api_client: TestClient
    ):
        creator = setup_rank_test["creator"]
        participant = setup_rank_test["participant"]
        space_id = setup_rank_test["space_id"]
        task1_id = setup_rank_test["task1_id"]
        task2_id = setup_rank_test["task2_id"]
        membership1_id = setup_rank_test["membership1_id"]
        submission1_id = setup_rank_test["submission1_id"]

        api_client.patch(
            f"/spaces/{space_id}",
            json={"enableRank": True},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        api_client.post(
            f"/tasks/{task1_id}/participants/{membership1_id}/submissions/{submission1_id}/review",
            json={"accepted": True, "score": 5, "comment": "Well done!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.post(
            f"/tasks/{task2_id}/participants",
            params={"member": participant.user_id},
            json={},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_rank_visible_in_space_response(self, setup_rank_test: dict, api_client: TestClient):
        creator = setup_rank_test["creator"]
        participant = setup_rank_test["participant"]
        space_id = setup_rank_test["space_id"]

        api_client.patch(
            f"/spaces/{space_id}",
            json={"enableRank": True},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.get(
            f"/spaces/{space_id}",
            params={"queryMyRank": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "myRank" in data
        assert isinstance(data["myRank"], int)

    def test_rank_disabled_returns_zero_or_null(
        self, setup_rank_test: dict, api_client: TestClient
    ):
        participant = setup_rank_test["participant"]
        space_id = setup_rank_test["space_id"]

        resp = api_client.get(
            f"/spaces/{space_id}",
            params={"queryMyRank": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data.get("myRank") in [0, None]

    def test_task_has_rank_field(self, setup_rank_test: dict, api_client: TestClient):
        creator = setup_rank_test["creator"]
        task1_id = setup_rank_test["task1_id"]
        task2_id = setup_rank_test["task2_id"]

        resp1 = api_client.get(
            f"/tasks/{task1_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp1.status_code == 200
        task1 = resp1.json()["data"]["task"]
        assert task1.get("rank") == 1

        resp2 = api_client.get(
            f"/tasks/{task2_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp2.status_code == 200
        task2 = resp2.json()["data"]["task"]
        assert task2.get("rank") == 2

    def test_update_space_disable_rank(self, setup_rank_test: dict, api_client: TestClient):
        creator = setup_rank_test["creator"]
        space_id = setup_rank_test["space_id"]

        api_client.patch(
            f"/spaces/{space_id}",
            json={"enableRank": True},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        get_resp1 = api_client.get(
            f"/spaces/{space_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert get_resp1.json()["data"]["space"]["enableRank"] is True

        resp = api_client.patch(
            f"/spaces/{space_id}",
            json={"enableRank": False},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200

        get_resp2 = api_client.get(
            f"/spaces/{space_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert get_resp2.json()["data"]["space"]["enableRank"] is False

    def test_enumerate_spaces_with_rank_disabled(
        self, setup_rank_test: dict, api_client: TestClient
    ):
        participant = setup_rank_test["participant"]
        space_id = setup_rank_test["space_id"]

        resp = api_client.get(
            "/spaces",
            params={"queryMyRank": "true", "pageSize": 50},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "spaces" in data
        target_space = next((s for s in data["spaces"] if s["id"] == space_id), None)
        if target_space:
            assert target_space.get("myRank") in [0, None]

    def test_enumerate_spaces_with_rank_enabled(
        self, setup_rank_test: dict, api_client: TestClient
    ):
        creator = setup_rank_test["creator"]
        participant = setup_rank_test["participant"]
        space_id = setup_rank_test["space_id"]

        api_client.patch(
            f"/spaces/{space_id}",
            json={"enableRank": True},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.get(
            "/spaces",
            params={"queryMyRank": "true", "pageSize": 50},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "spaces" in data
        target_space = next((s for s in data["spaces"] if s["id"] == space_id), None)
        assert target_space is not None, f"Space {space_id} not found in response"
        assert target_space.get("myRank") == 0

    def test_rank2_review_upgrades_to_rank2(self, user_client: UserCreator, api_client: TestClient):
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)
        participant = user_client.create_user()
        participant.token = user_client.login(
            api_client, participant.username, participant.password
        )

        suffix = unique_int(10000000, 99999999)
        deadline = int((datetime.now(UTC).timestamp() + 7 * 24 * 3600) * 1000)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Rank Prog Space ({suffix})",
                "intro": "Test space",
                "description": "Description",
                "avatarId": 1,
                "enableRank": True,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        space_id = space_resp.json()["data"]["space"]["id"]
        default_category_id = space_resp.json()["data"]["space"]["defaultCategoryId"]

        task1_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Rank 1 Task ({suffix})",
                "submitterType": "USER",
                "deadline": deadline,
                "resubmittable": False,
                "editable": False,
                "intro": "Task intro " * 10,
                "description": "Task description " * 10,
                "submissionSchema": [{"prompt": "Text Entry", "type": "TEXT"}],
                "space": space_id,
                "categoryId": default_category_id,
                "rank": 1,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        task1_id = task1_resp.json()["data"]["task"]["id"]
        api_client.patch(
            f"/tasks/{task1_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        task2_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Rank 2 Task ({suffix})",
                "submitterType": "USER",
                "deadline": deadline,
                "resubmittable": False,
                "editable": False,
                "intro": "Task intro " * 10,
                "description": "Task description " * 10,
                "submissionSchema": [{"prompt": "Text Entry", "type": "TEXT"}],
                "space": space_id,
                "categoryId": default_category_id,
                "rank": 2,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        task2_id = task2_resp.json()["data"]["task"]["id"]
        api_client.patch(
            f"/tasks/{task2_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        join1 = api_client.post(
            f"/tasks/{task1_id}/participants",
            params={"member": participant.user_id},
            json={},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        m1_id = join1.json()["data"]["participant"]["id"]
        api_client.patch(
            f"/tasks/{task1_id}/participants/{m1_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        sub1 = api_client.post(
            f"/tasks/{task1_id}/participants/{m1_id}/submissions",
            json=[{"text": "Test"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        s1_id = sub1.json()["data"]["submission"]["id"]
        api_client.post(
            f"/tasks/{task1_id}/participants/{m1_id}/submissions/{s1_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        join2 = api_client.post(
            f"/tasks/{task2_id}/participants",
            params={"member": participant.user_id},
            json={},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        m2_id = join2.json()["data"]["participant"]["id"]
        api_client.patch(
            f"/tasks/{task2_id}/participants/{m2_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        sub2 = api_client.post(
            f"/tasks/{task2_id}/participants/{m2_id}/submissions",
            json=[{"text": "Test"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        s2_id = sub2.json()["data"]["submission"]["id"]
        api_client.post(
            f"/tasks/{task2_id}/participants/{m2_id}/submissions/{s2_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        space_resp = api_client.get(
            f"/spaces/{space_id}",
            params={"queryMyRank": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert space_resp.json()["data"]["myRank"] == 2

    def test_another_rank1_task_does_not_upgrade_further(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)
        participant = user_client.create_user()
        participant.token = user_client.login(
            api_client, participant.username, participant.password
        )

        suffix = unique_int(10000000, 99999999)
        deadline = int((datetime.now(UTC).timestamp() + 7 * 24 * 3600) * 1000)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Another Rank Space ({suffix})",
                "intro": "Test space",
                "description": "Description",
                "avatarId": 1,
                "enableRank": True,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        space_id = space_resp.json()["data"]["space"]["id"]
        default_category_id = space_resp.json()["data"]["space"]["defaultCategoryId"]

        task1_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Rank 1 Task A ({suffix})",
                "submitterType": "USER",
                "deadline": deadline,
                "resubmittable": False,
                "editable": False,
                "intro": "Task intro " * 10,
                "description": "Task description " * 10,
                "submissionSchema": [{"prompt": "Text Entry", "type": "TEXT"}],
                "space": space_id,
                "categoryId": default_category_id,
                "rank": 1,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        task1_id = task1_resp.json()["data"]["task"]["id"]
        api_client.patch(
            f"/tasks/{task1_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        task2_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Rank 1 Task B ({suffix})",
                "submitterType": "USER",
                "deadline": deadline,
                "resubmittable": False,
                "editable": False,
                "intro": "Task intro " * 10,
                "description": "Task description " * 10,
                "submissionSchema": [{"prompt": "Text Entry", "type": "TEXT"}],
                "space": space_id,
                "categoryId": default_category_id,
                "rank": 1,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        task2_id = task2_resp.json()["data"]["task"]["id"]
        api_client.patch(
            f"/tasks/{task2_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        join1 = api_client.post(
            f"/tasks/{task1_id}/participants",
            params={"member": participant.user_id},
            json={},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        m1_id = join1.json()["data"]["participant"]["id"]
        api_client.patch(
            f"/tasks/{task1_id}/participants/{m1_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        sub1 = api_client.post(
            f"/tasks/{task1_id}/participants/{m1_id}/submissions",
            json=[{"text": "Test"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        s1_id = sub1.json()["data"]["submission"]["id"]
        api_client.post(
            f"/tasks/{task1_id}/participants/{m1_id}/submissions/{s1_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        space_check1 = api_client.get(
            f"/spaces/{space_id}",
            params={"queryMyRank": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert space_check1.json()["data"]["myRank"] == 1

        join2 = api_client.post(
            f"/tasks/{task2_id}/participants",
            params={"member": participant.user_id},
            json={},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        m2_id = join2.json()["data"]["participant"]["id"]
        api_client.patch(
            f"/tasks/{task2_id}/participants/{m2_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        sub2 = api_client.post(
            f"/tasks/{task2_id}/participants/{m2_id}/submissions",
            json=[{"text": "Test"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        s2_id = sub2.json()["data"]["submission"]["id"]
        review_resp = api_client.post(
            f"/tasks/{task2_id}/participants/{m2_id}/submissions/{s2_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert review_resp.json()["data"]["review"].get("hasUpgradedParticipantRank") is False

        space_check2 = api_client.get(
            f"/spaces/{space_id}",
            params={"queryMyRank": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert space_check2.json()["data"]["myRank"] == 1

    def test_rank_remains_zero_after_failing_review(
        self, setup_rank_test: dict, api_client: TestClient
    ):
        creator = setup_rank_test["creator"]
        participant = setup_rank_test["participant"]
        space_id = setup_rank_test["space_id"]
        default_category_id = setup_rank_test["default_category_id"]

        api_client.patch(
            f"/spaces/{space_id}",
            json={"enableRank": True},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        deadline = int((datetime.now(UTC).timestamp() + 7 * 24 * 3600) * 1000)
        suffix = unique_int(10000000, 99999999)

        task_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Rank 1 Fail Task ({suffix})",
                "submitterType": "USER",
                "deadline": deadline,
                "resubmittable": False,
                "editable": False,
                "intro": "Task intro " * 10,
                "description": "Task description " * 10,
                "submissionSchema": [{"prompt": "Text Entry", "type": "TEXT"}],
                "space": space_id,
                "categoryId": default_category_id,
                "rank": 1,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        task_id = task_resp.json()["data"]["task"]["id"]
        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        join_resp = api_client.post(
            f"/tasks/{task_id}/participants",
            params={"member": participant.user_id},
            json={},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        membership_id = join_resp.json()["data"]["participant"]["id"]
        api_client.patch(
            f"/tasks/{task_id}/participants/{membership_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        sub_resp = api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "My submission"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        submission_id = sub_resp.json()["data"]["submission"]["id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": False, "score": 2, "comment": "Not good enough"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        space_resp = api_client.get(
            f"/spaces/{space_id}",
            params={"queryMyRank": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert space_resp.status_code == 200
        my_rank = space_resp.json()["data"].get("myRank", 0)
        assert my_rank == 0, f"Expected rank 0 after failing review, got {my_rank}"
