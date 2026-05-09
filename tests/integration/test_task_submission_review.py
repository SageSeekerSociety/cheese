from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, unique_int


class TestTaskSubmissionReviewIntegration:
    @pytest.fixture
    def setup_submission(self, user_client: UserCreator, api_client: TestClient) -> dict:
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
                "name": f"Review Test Space ({suffix})",
                "intro": "Test space for reviews",
                "description": "A lengthy description. " * 20,
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
        category_id = space_data["defaultCategoryId"]

        deadline = int((datetime.now(UTC).timestamp() + 7 * 24 * 3600) * 1000)

        task_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Review Test Task ({suffix})",
                "submitterType": "USER",
                "deadline": deadline,
                "resubmittable": False,
                "editable": False,
                "intro": "Task intro " * 10,
                "description": "Task description " * 10,
                "submissionSchema": [
                    {"prompt": "Text Entry", "type": "TEXT"},
                ],
                "space": space_id,
                "categoryId": category_id,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert task_resp.status_code == 200, f"Task creation failed: {task_resp.text}"
        task_id = task_resp.json()["data"]["task"]["id"]

        approve_resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert approve_resp.status_code == 200, (
            f"Task approval failed: {approve_resp.status_code}: {approve_resp.text}"
        )

        join_resp = api_client.post(
            f"/tasks/{task_id}/participants",
            params={"member": participant.user_id},
            json={},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert join_resp.status_code == 200, f"Join task failed: {join_resp.text}"
        membership_id = join_resp.json()["data"]["participant"]["id"]

        approve_member_resp = api_client.patch(
            f"/tasks/{task_id}/participants/{membership_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert approve_member_resp.status_code == 200, (
            f"Member approval failed: {approve_member_resp.status_code}: {approve_member_resp.text}"
        )

        submit_resp = api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "This is a test submission."}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert submit_resp.status_code == 200, f"Submit failed: {submit_resp.text}"
        submission_id = submit_resp.json()["data"]["submission"]["id"]

        return {
            "creator": creator,
            "participant": participant,
            "space_id": space_id,
            "task_id": task_id,
            "membership_id": membership_id,
            "submission_id": submission_id,
        }

    def test_get_submissions_not_reviewed(self, setup_submission: dict, api_client: TestClient):
        participant = setup_submission["participant"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            params={"queryReview": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "submissions" in data
        submissions = data["submissions"]
        assert len(submissions) == 1
        assert submissions[0]["review"]["reviewed"] is False
        assert submissions[0]["review"].get("detail") is None

    def test_get_submissions_filter_reviewed_true_when_not_reviewed(
        self, setup_submission: dict, api_client: TestClient
    ):
        participant = setup_submission["participant"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            params={"queryReview": "true", "reviewed": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert data["submissions"] == []

    def test_get_submissions_filter_reviewed_false_when_not_reviewed(
        self, setup_submission: dict, api_client: TestClient
    ):
        participant = setup_submission["participant"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            params={"queryReview": "true", "reviewed": "false"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        submissions = data["submissions"]
        assert len(submissions) == 1
        assert submissions[0]["review"]["reviewed"] is False

    def test_create_review_success(self, setup_submission: dict, api_client: TestClient):
        creator = setup_submission["creator"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        resp = api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        review = data["review"]
        assert review["reviewed"] is True
        assert review["detail"]["accepted"] is True
        assert review["detail"]["score"] == 5
        assert review["detail"]["comment"] == "Good job!"

    def test_create_review_forbidden_for_participant(
        self, setup_submission: dict, api_client: TestClient
    ):
        participant = setup_submission["participant"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        resp = api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"

    def test_create_review_conflict_when_already_reviewed(
        self, setup_submission: dict, api_client: TestClient
    ):
        creator = setup_submission["creator"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"

    def test_update_review_empty_request(self, setup_submission: dict, api_client: TestClient):
        creator = setup_submission["creator"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.patch(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        review = data["review"]
        assert review["reviewed"] is True
        assert review["detail"]["accepted"] is True
        assert review["detail"]["score"] == 5
        assert review["detail"]["comment"] == "Good job!"

    def test_update_review_success(self, setup_submission: dict, api_client: TestClient):
        creator = setup_submission["creator"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.patch(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": False, "score": 4, "comment": "Could be better."},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        review = data["review"]
        assert review["reviewed"] is True
        assert review["detail"]["accepted"] is False
        assert review["detail"]["score"] == 4
        assert review["detail"]["comment"] == "Could be better."

    def test_update_review_forbidden_for_participant(
        self, setup_submission: dict, api_client: TestClient
    ):
        creator = setup_submission["creator"]
        participant = setup_submission["participant"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.patch(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": False, "score": 4, "comment": "Could be better."},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"

    def test_get_submissions_after_review_update(
        self, setup_submission: dict, api_client: TestClient
    ):
        creator = setup_submission["creator"]
        participant = setup_submission["participant"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        api_client.patch(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": False, "score": 4, "comment": "Could be better."},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            params={"queryReview": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        submissions = data["submissions"]
        assert len(submissions) == 1
        assert submissions[0]["review"]["reviewed"] is True
        assert submissions[0]["review"]["detail"]["accepted"] is False
        assert submissions[0]["review"]["detail"]["score"] == 4
        assert submissions[0]["review"]["detail"]["comment"] == "Could be better."

    def test_get_submissions_filter_reviewed_true_after_update(
        self, setup_submission: dict, api_client: TestClient
    ):
        creator = setup_submission["creator"]
        participant = setup_submission["participant"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            params={"queryReview": "true", "reviewed": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        submissions = data["submissions"]
        assert len(submissions) == 1
        assert submissions[0]["review"]["reviewed"] is True

    def test_get_submissions_filter_reviewed_false_after_update(
        self, setup_submission: dict, api_client: TestClient
    ):
        creator = setup_submission["creator"]
        participant = setup_submission["participant"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            params={"queryReview": "true", "reviewed": "false"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert data["submissions"] == []

    def test_delete_review_forbidden_for_participant(
        self, setup_submission: dict, api_client: TestClient
    ):
        creator = setup_submission["creator"]
        participant = setup_submission["participant"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.delete(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"

    def test_delete_review_success(self, setup_submission: dict, api_client: TestClient):
        creator = setup_submission["creator"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.delete(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_get_submissions_after_review_delete(
        self, setup_submission: dict, api_client: TestClient
    ):
        creator = setup_submission["creator"]
        participant = setup_submission["participant"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        api_client.delete(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            params={"queryReview": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        submissions = data["submissions"]
        assert len(submissions) == 1
        assert submissions[0]["review"]["reviewed"] is False

    def test_delete_review_not_found_when_already_deleted(
        self, setup_submission: dict, api_client: TestClient
    ):
        creator = setup_submission["creator"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        api_client.delete(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.delete(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"

    def test_update_review_not_found_when_deleted(
        self, setup_submission: dict, api_client: TestClient
    ):
        creator = setup_submission["creator"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        api_client.delete(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.patch(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": False, "score": 4, "comment": "Could be better."},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"

    def test_create_review_again_after_deletion(
        self, setup_submission: dict, api_client: TestClient
    ):
        creator = setup_submission["creator"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        api_client.delete(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        review = data["review"]
        assert review["reviewed"] is True
        assert review["detail"]["accepted"] is True
        assert review["detail"]["score"] == 5
        assert review["detail"]["comment"] == "Good job!"

    def test_get_submissions_after_review_recreation(
        self, setup_submission: dict, api_client: TestClient
    ):
        creator = setup_submission["creator"]
        participant = setup_submission["participant"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        api_client.delete(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            params={"queryReview": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        submissions = data["submissions"]
        assert len(submissions) == 1
        assert submissions[0]["review"]["reviewed"] is True
        assert submissions[0]["review"]["detail"]["accepted"] is True
        assert submissions[0]["review"]["detail"]["score"] == 5
        assert submissions[0]["review"]["detail"]["comment"] == "Good job!"

    def test_get_submissions_filter_reviewed_true_after_recreation(
        self, setup_submission: dict, api_client: TestClient
    ):
        creator = setup_submission["creator"]
        participant = setup_submission["participant"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        api_client.delete(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            params={"queryReview": "true", "reviewed": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        submissions = data["submissions"]
        assert len(submissions) == 1
        assert submissions[0]["review"]["reviewed"] is True
        assert submissions[0]["review"]["detail"]["accepted"] is True
        assert submissions[0]["review"]["detail"]["score"] == 5
        assert submissions[0]["review"]["detail"]["comment"] == "Good job!"

    def test_get_submissions_filter_reviewed_false_after_recreation(
        self, setup_submission: dict, api_client: TestClient
    ):
        creator = setup_submission["creator"]
        participant = setup_submission["participant"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        api_client.delete(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good job!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            params={"queryReview": "true", "reviewed": "false"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert data["submissions"] == []

    def test_get_review_directly_after_creation(
        self, setup_submission: dict, api_client: TestClient
    ):
        creator = setup_submission["creator"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 10, "comment": "Excellent work!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        review = data["review"]
        assert review["reviewed"] is True
        assert review["detail"]["accepted"] is True
        assert review["detail"]["score"] == 10
        assert review["detail"]["comment"] == "Excellent work!"

    def test_get_review_not_found_after_deletion(
        self, setup_submission: dict, api_client: TestClient
    ):
        creator = setup_submission["creator"]
        task_id = setup_submission["task_id"]
        membership_id = setup_submission["membership_id"]
        submission_id = setup_submission["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            json={"accepted": True, "score": 5, "comment": "Good!"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        api_client.delete(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
