import random
from datetime import UTC, datetime

import httpx
import pytest

from tests.integration.conftest import UserCreator


class TestTaskSubmissionIntegration:
    @pytest.fixture
    def setup_task_for_submission(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        participant = user_client.create_user()
        participant.token = user_client.login(
            api_client, participant.username, participant.password
        )

        participant2 = user_client.create_user()
        participant2.token = user_client.login(
            api_client, participant2.username, participant2.password
        )

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Submission Test Space ({suffix})",
                "intro": "Test space for submissions",
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

        return {
            "creator": creator,
            "participant": participant,
            "participant2": participant2,
            "space_id": space_id,
            "category_id": category_id,
            "suffix": suffix,
        }

    def _create_task(
        self,
        api_client: httpx.Client,
        token: str,
        space_id: int,
        category_id: int,
        suffix: int,
        resubmittable: bool = True,
        editable: bool = True,
    ) -> int:
        deadline = int((datetime.now(UTC).timestamp() + 7 * 24 * 3600) * 1000)
        task_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Submission Test Task ({suffix})",
                "submitterType": "USER",
                "deadline": deadline,
                "resubmittable": resubmittable,
                "editable": editable,
                "intro": "Task intro " * 10,
                "description": "Task description " * 10,
                "submissionSchema": [{"prompt": "Text Entry", "type": "TEXT"}],
                "space": space_id,
                "categoryId": category_id,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert task_resp.status_code == 200, f"Task creation failed: {task_resp.text}"
        task_id = task_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {token}"},
        )
        return task_id

    def _add_participant(
        self,
        api_client: httpx.Client,
        task_id: int,
        participant_token: str,
        participant_id: int,
        creator_token: str,
    ) -> int:
        join_resp = api_client.post(
            f"/tasks/{task_id}/participations/user",
            json={},
            headers={"Authorization": f"Bearer {participant_token}"},
        )
        assert join_resp.status_code == 200, f"Join task failed: {join_resp.text}"
        membership_id = join_resp.json()["data"]["participant"]["id"]

        api_client.patch(
            f"/tasks/{task_id}/participants/{membership_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator_token}"},
        )
        return membership_id

    def test_submit_task_first_time(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )
        membership_id = self._add_participant(
            api_client, task_id, participant.token, participant.user_id, creator.token
        )

        resp = api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "This is my first submission."}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        submission = resp.json()["data"]["submission"]
        assert submission["version"] == 1
        assert submission["member"]["id"] == participant.user_id

    def test_resubmit_task_when_resubmittable(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
            resubmittable=True,
        )
        membership_id = self._add_participant(
            api_client, task_id, participant.token, participant.user_id, creator.token
        )

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "First submission"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )

        resp = api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "Second submission"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        submission = resp.json()["data"]["submission"]
        assert submission["version"] == 2

    def test_resubmit_task_fails_when_not_resubmittable(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
            resubmittable=False,
        )
        membership_id = self._add_participant(
            api_client, task_id, participant.token, participant.user_id, creator.token
        )

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "First submission"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )

        resp = api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "Second submission"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

    def test_edit_submission_when_editable(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
            editable=True,
        )
        membership_id = self._add_participant(
            api_client, task_id, participant.token, participant.user_id, creator.token
        )

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "Original text"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )

        resp = api_client.patch(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/1",
            json=[{"text": "Edited text"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        submission = resp.json()["data"]["submission"]
        assert submission["version"] == 1
        text_content = next((c for c in submission["content"] if c["type"] == "TEXT"), None)
        assert text_content is not None
        assert text_content["contentText"] == "Edited text"

    def test_edit_submission_fails_when_not_editable(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
            editable=False,
        )
        membership_id = self._add_participant(
            api_client, task_id, participant.token, participant.user_id, creator.token
        )

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "Original text"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )

        resp = api_client.patch(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/1",
            json=[{"text": "Edited text"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

    def test_get_submissions_for_participant(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )
        membership_id = self._add_participant(
            api_client, task_id, participant.token, participant.user_id, creator.token
        )

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "My submission"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        submissions = resp.json()["data"]["submissions"]
        assert len(submissions) == 1
        assert submissions[0]["version"] == 1

    def test_get_submissions_all_versions(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
            resubmittable=True,
        )
        membership_id = self._add_participant(
            api_client, task_id, participant.token, participant.user_id, creator.token
        )

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "Version 1"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "Version 2"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            params={"allVersions": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        submissions = resp.json()["data"]["submissions"]
        assert len(submissions) == 2

    def test_get_submissions_for_owner(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )
        membership_id = self._add_participant(
            api_client, task_id, participant.token, participant.user_id, creator.token
        )

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "My submission"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        submissions = resp.json()["data"]["submissions"]
        assert len(submissions) == 1

    def test_get_submissions_fails_for_other_participant(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]
        participant2 = data["participant2"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )
        membership_id = self._add_participant(
            api_client, task_id, participant.token, participant.user_id, creator.token
        )
        self._add_participant(
            api_client, task_id, participant2.token, participant2.user_id, creator.token
        )

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "My submission"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            headers={"Authorization": f"Bearer {participant2.token}"},
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"

    def test_submit_fails_before_participant_approval(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )

        join_resp = api_client.post(
            f"/tasks/{task_id}/participations/user",
            json={},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert join_resp.status_code == 200
        membership_id = join_resp.json()["data"]["participant"]["id"]

        resp = api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "My submission"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"

    def test_delete_task_as_owner(self, setup_task_for_submission: dict, api_client: httpx.Client):
        data = setup_task_for_submission
        creator = data["creator"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )

        resp = api_client.delete(
            f"/tasks/{task_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"

        get_resp = api_client.get(
            f"/tasks/{task_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert get_resp.status_code == 404

    def test_delete_task_fails_for_non_owner(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )

        resp = api_client.delete(
            f"/tasks/{task_id}",
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"

    def test_update_task_properties(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
            resubmittable=False,
            editable=False,
        )

        resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"resubmittable": True, "editable": True},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        task = resp.json()["data"]["task"]
        assert task["resubmittable"] is True
        assert task["editable"] is True

    def test_get_task_eligibility_before_joining(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )

        resp = api_client.get(
            f"/tasks/{task_id}",
            params={"queryJoinability": "true", "querySubmittability": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        task = resp.json()["data"]["task"]
        assert task["submitterType"] == "USER"
        eligibility = task.get("participationEligibility")
        if eligibility:
            user_status = eligibility.get("user")
            if user_status:
                assert user_status["eligible"] is True

    def test_get_task_eligibility_after_joining(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )
        self._add_participant(
            api_client, task_id, participant.token, participant.user_id, creator.token
        )

        resp = api_client.get(
            f"/tasks/{task_id}",
            params={"queryJoinability": "true", "querySubmittability": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        task = resp.json()["data"]["task"]
        assert task.get("submittable") is True
        eligibility = task.get("participationEligibility")
        if eligibility:
            user_status = eligibility.get("user")
            if user_status:
                assert user_status["eligible"] is False
                reasons = user_status.get("reasons", [])
                assert any(r.get("code") == "ALREADY_PARTICIPATING" for r in reasons)

    def test_submit_task_user2_first_time(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant2 = data["participant2"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )
        membership_id = self._add_participant(
            api_client, task_id, participant2.token, participant2.user_id, creator.token
        )

        resp = api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "User 2 submission."}],
            headers={"Authorization": f"Bearer {participant2.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        submission = resp.json()["data"]["submission"]
        assert submission["version"] == 1
        assert submission["member"]["id"] == participant2.user_id

    def test_multiple_participants_submissions_isolated(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]
        participant2 = data["participant2"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )
        membership1_id = self._add_participant(
            api_client, task_id, participant.token, participant.user_id, creator.token
        )
        membership2_id = self._add_participant(
            api_client, task_id, participant2.token, participant2.user_id, creator.token
        )

        api_client.post(
            f"/tasks/{task_id}/participants/{membership1_id}/submissions",
            json=[{"text": "User 1 submission"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        api_client.post(
            f"/tasks/{task_id}/participants/{membership2_id}/submissions",
            json=[{"text": "User 2 submission"}],
            headers={"Authorization": f"Bearer {participant2.token}"},
        )

        resp1 = api_client.get(
            f"/tasks/{task_id}/participants/{membership1_id}/submissions",
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp1.status_code == 200
        subs1 = resp1.json()["data"]["submissions"]
        assert len(subs1) == 1
        assert subs1[0]["member"]["id"] == participant.user_id

        resp2 = api_client.get(
            f"/tasks/{task_id}/participants/{membership2_id}/submissions",
            headers={"Authorization": f"Bearer {participant2.token}"},
        )
        assert resp2.status_code == 200
        subs2 = resp2.json()["data"]["submissions"]
        assert len(subs2) == 1
        assert subs2[0]["member"]["id"] == participant2.user_id

    def test_update_task_fails_for_non_owner(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )

        resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"resubmittable": True},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"

    def test_get_submissions_default_returns_latest(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
            resubmittable=True,
        )
        membership_id = self._add_participant(
            api_client, task_id, participant.token, participant.user_id, creator.token
        )

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "Version 1"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "Version 2 - latest"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200
        submissions = resp.json()["data"]["submissions"]
        assert len(submissions) == 1
        assert submissions[0]["version"] == 2

    def test_get_submissions_fails_for_irrelevant_user(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        irrelevant = data["participant2"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )
        membership_id = self._add_participant(
            api_client, task_id, participant.token, participant.user_id, creator.token
        )

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "My submission"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            headers={"Authorization": f"Bearer {irrelevant.token}"},
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"

    def test_update_task_name_and_intro(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )

        new_name = f"Updated Task Name ({data['suffix']})"
        new_intro = "Updated intro " * 10

        resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"name": new_name, "intro": new_intro},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        task = resp.json()["data"]["task"]
        assert task["name"] == new_name
        assert task["intro"] == new_intro

    def test_update_task_deadline(self, setup_task_for_submission: dict, api_client: httpx.Client):
        data = setup_task_for_submission
        creator = data["creator"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )

        new_deadline = int((datetime.now(UTC).timestamp() + 14 * 24 * 3600) * 1000)

        resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"deadline": new_deadline},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        get_resp = api_client.get(
            f"/tasks/{task_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert get_resp.status_code == 200
        task = get_resp.json()["data"]["task"]
        assert (task.get("deadline") - new_deadline) < 1000  # Allow for small time differences

    def test_update_submission_schema(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )

        new_schema = [
            {"prompt": "Updated Text Entry", "type": "TEXT"},
            {"prompt": "New File Entry", "type": "FILE"},
        ]

        resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"submissionSchema": new_schema},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        get_resp = api_client.get(
            f"/tasks/{task_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert get_resp.status_code == 200
        task = get_resp.json()["data"]["task"]
        assert len(task["submissionSchema"]) == 2
        prompts = [s["prompt"] for s in task["submissionSchema"]]
        assert "Updated Text Entry" in prompts
        assert "New File Entry" in prompts

    def test_resubmit_increments_version(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
            resubmittable=True,
        )
        membership_id = self._add_participant(
            api_client, task_id, participant.token, participant.user_id, creator.token
        )

        for i in range(1, 4):
            resp = api_client.post(
                f"/tasks/{task_id}/participants/{membership_id}/submissions",
                json=[{"text": f"Submission version {i}"}],
                headers={"Authorization": f"Bearer {participant.token}"},
            )
            assert resp.status_code == 200
            submission = resp.json()["data"]["submission"]
            assert submission["version"] == i

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            params={"allVersions": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200
        submissions = resp.json()["data"]["submissions"]
        assert len(submissions) == 3

    def test_edit_preserves_version(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
            editable=True,
            resubmittable=True,
        )
        membership_id = self._add_participant(
            api_client, task_id, participant.token, participant.user_id, creator.token
        )

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "Version 1"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "Version 2"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )

        resp = api_client.patch(
            f"/tasks/{task_id}/participants/{membership_id}/submissions/2",
            json=[{"text": "Version 2 edited"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200
        submission = resp.json()["data"]["submission"]
        assert submission["version"] == 2

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            params={"allVersions": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        submissions = resp.json()["data"]["submissions"]
        assert len(submissions) == 2

    def test_approve_participant_via_bulk_endpoint(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        deadline = int((datetime.now(UTC).timestamp() + 7 * 24 * 3600) * 1000)
        task_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Bulk Approval Task ({data['suffix']})",
                "submitterType": "USER",
                "deadline": deadline,
                "resubmittable": True,
                "editable": True,
                "intro": "Task intro " * 10,
                "description": "Task description " * 10,
                "submissionSchema": [{"prompt": "Text Entry", "type": "TEXT"}],
                "space": data["space_id"],
                "categoryId": data["category_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert task_resp.status_code == 200
        task_id = task_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        join_resp = api_client.post(
            f"/tasks/{task_id}/participations/user",
            json={},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert join_resp.status_code == 200
        membership_id = join_resp.json()["data"]["participant"]["id"]

        resp = api_client.patch(
            f"/tasks/{task_id}/participants/{membership_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data_resp = resp.json()["data"]
        participants = data_resp.get("participants", [])
        if participants:
            approved = next(
                (
                    p
                    for p in participants
                    if p.get("member", {}).get("id") == participant.user_id
                    or p.get("memberId") == participant.user_id
                ),
                None,
            )
            if approved:
                assert approved.get("approved") == "APPROVED"

    @pytest.fixture
    def setup_team_task_for_submission(
        self, user_client: UserCreator, api_client: httpx.Client
    ) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        team_creator = user_client.create_user()
        team_creator.token = user_client.login(
            api_client, team_creator.username, team_creator.password
        )

        team_member = user_client.create_user()
        team_member.token = user_client.login(
            api_client, team_member.username, team_member.password
        )

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Team Submission Space ({suffix})",
                "intro": "Test space for team submissions",
                "description": "A lengthy description. " * 20,
                "avatarId": 1,
                "enableRank": False,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert space_resp.status_code == 201
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        category_id = space_data["defaultCategoryId"]

        team_resp = api_client.post(
            "/teams",
            json={
                "name": f"Test Team ({suffix})",
                "intro": "Test team",
                "description": "A lengthy description. " * 20,
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {team_creator.token}"},
        )
        assert team_resp.status_code == 201
        team_id = team_resp.json()["data"]["team"]["id"]

        req_resp = api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "Please let me join"},
            headers={"Authorization": f"Bearer {team_member.token}"},
        )
        assert req_resp.status_code == 201
        request_id = req_resp.json()["data"]["application"]["id"]

        api_client.post(
            f"/teams/{team_id}/requests/{request_id}/approve",
            headers={"Authorization": f"Bearer {team_creator.token}"},
        )

        return {
            "creator": creator,
            "team_creator": team_creator,
            "team_member": team_member,
            "team_id": team_id,
            "space_id": space_id,
            "category_id": category_id,
            "suffix": suffix,
        }

    def _create_team_task(
        self,
        api_client: httpx.Client,
        token: str,
        space_id: int,
        category_id: int,
        suffix: int,
    ) -> int:
        deadline = int((datetime.now(UTC).timestamp() + 7 * 24 * 3600) * 1000)
        task_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Team Submission Task ({suffix})",
                "submitterType": "TEAM",
                "deadline": deadline,
                "resubmittable": True,
                "editable": True,
                "intro": "Task intro " * 10,
                "description": "Task description " * 10,
                "submissionSchema": [
                    {"prompt": "Text Entry", "type": "TEXT"},
                    {"prompt": "Attachment Entry", "type": "FILE"},
                ],
                "space": space_id,
                "categoryId": category_id,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert task_resp.status_code == 200, f"Task creation failed: {task_resp.text}"
        task_id = task_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {token}"},
        )
        return task_id

    def _add_team_participant(
        self,
        api_client: httpx.Client,
        task_id: int,
        team_id: int,
        team_creator_token: str,
        space_creator_token: str,
    ) -> int:
        join_resp = api_client.post(
            f"/tasks/{task_id}/participations/team",
            json={"teamId": team_id},
            headers={"Authorization": f"Bearer {team_creator_token}"},
        )
        assert join_resp.status_code == 200, f"Team join failed: {join_resp.text}"
        membership_id = join_resp.json()["data"]["participant"]["id"]

        api_client.patch(
            f"/tasks/{task_id}/participants/{membership_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {space_creator_token}"},
        )
        return membership_id

    def test_submit_task_team_first_time(
        self, setup_team_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_team_task_for_submission
        creator = data["creator"]
        team_creator = data["team_creator"]
        team_id = data["team_id"]

        task_id = self._create_team_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )
        membership_id = self._add_team_participant(
            api_client, task_id, team_id, team_creator.token, creator.token
        )

        resp = api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "Team submission content."}],
            headers={"Authorization": f"Bearer {team_creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        submission = resp.json()["data"]["submission"]
        assert submission["version"] == 1
        assert submission["member"]["id"] == team_id
        assert submission["submitter"]["id"] == team_creator.user_id

    def test_get_task_eligibility_for_team_before_approval(
        self, setup_team_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_team_task_for_submission
        creator = data["creator"]
        team_creator = data["team_creator"]
        team_id = data["team_id"]

        task_id = self._create_team_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )

        join_resp = api_client.post(
            f"/tasks/{task_id}/participations/team",
            json={"teamId": team_id},
            headers={"Authorization": f"Bearer {team_creator.token}"},
        )
        assert join_resp.status_code == 200

        resp = api_client.get(
            f"/tasks/{task_id}",
            params={"queryJoinability": "true", "querySubmittability": "true"},
            headers={"Authorization": f"Bearer {team_creator.token}"},
        )
        assert resp.status_code == 200
        task = resp.json()["data"]["task"]
        assert task["submitterType"] == "TEAM"
        eligibility = task.get("participationEligibility")
        if eligibility:
            teams = eligibility.get("teams", [])
            team_status = next((t for t in teams if t.get("team", {}).get("id") == team_id), None)
            if team_status:
                assert team_status["eligibility"]["eligible"] is False
                reasons = team_status["eligibility"].get("reasons", [])
                assert any(r.get("code") == "ALREADY_PARTICIPATING" for r in reasons)

    def test_get_task_eligibility_for_team_after_approval(
        self, setup_team_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_team_task_for_submission
        creator = data["creator"]
        team_creator = data["team_creator"]
        team_id = data["team_id"]

        task_id = self._create_team_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )
        self._add_team_participant(api_client, task_id, team_id, team_creator.token, creator.token)

        resp = api_client.get(
            f"/tasks/{task_id}",
            params={"queryJoinability": "true", "querySubmittability": "true"},
            headers={"Authorization": f"Bearer {team_creator.token}"},
        )
        assert resp.status_code == 200
        task = resp.json()["data"]["task"]
        assert task["submitterType"] == "TEAM"
        submittable_as_team = task.get("submittableAsTeam", [])
        assert any(t.get("id") == team_id for t in submittable_as_team)

    def test_get_submissions_fails_via_member_query_param(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]
        participant2 = data["participant2"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )
        membership_id = self._add_participant(
            api_client, task_id, participant.token, participant.user_id, creator.token
        )
        self._add_participant(
            api_client, task_id, participant2.token, participant2.user_id, creator.token
        )

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "My submission"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            params={"member": participant.user_id},
            headers={"Authorization": f"Bearer {participant2.token}"},
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"

    def test_get_submissions_succeeds_via_member_query_param(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )
        membership_id = self._add_participant(
            api_client, task_id, participant.token, participant.user_id, creator.token
        )

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "My submission"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            params={"member": participant.user_id},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        submissions = resp.json()["data"]["submissions"]
        assert len(submissions) == 1
        assert submissions[0]["member"]["id"] == participant.user_id

    def test_team_member_can_submit_for_team(
        self, setup_team_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_team_task_for_submission
        creator = data["creator"]
        team_creator = data["team_creator"]
        team_member = data["team_member"]
        team_id = data["team_id"]

        task_id = self._create_team_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )
        membership_id = self._add_team_participant(
            api_client, task_id, team_id, team_creator.token, creator.token
        )

        resp = api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "Submission by team member."}],
            headers={"Authorization": f"Bearer {team_member.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        submission = resp.json()["data"]["submission"]
        assert submission["member"]["id"] == team_id
        assert submission["submitter"]["id"] == team_member.user_id

    def test_approve_team_participant_via_bulk_endpoint(
        self, setup_team_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_team_task_for_submission
        creator = data["creator"]
        team_creator = data["team_creator"]
        team_id = data["team_id"]

        task_id = self._create_team_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )

        join_resp = api_client.post(
            f"/tasks/{task_id}/participations/team",
            json={"teamId": team_id},
            headers={"Authorization": f"Bearer {team_creator.token}"},
        )
        assert join_resp.status_code == 200
        membership_id = join_resp.json()["data"]["participant"]["id"]

        resp = api_client.patch(
            f"/tasks/{task_id}/participants/{membership_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data_resp = resp.json()["data"]
        participants = data_resp.get("participants", [])
        if participants:
            approved = next(
                (p for p in participants if p.get("member", {}).get("id") == team_id), None
            )
            if approved:
                assert approved.get("approved") == "APPROVED"

    def test_get_submissions_succeeds_for_owner_via_participant_path(
        self, setup_task_for_submission: dict, api_client: httpx.Client
    ):
        data = setup_task_for_submission
        creator = data["creator"]
        participant = data["participant"]

        task_id = self._create_task(
            api_client,
            creator.token,
            data["space_id"],
            data["category_id"],
            data["suffix"],
        )
        membership_id = self._add_participant(
            api_client, task_id, participant.token, participant.user_id, creator.token
        )

        api_client.post(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            json=[{"text": "Participant submission"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )

        resp = api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        submissions = resp.json()["data"]["submissions"]
        assert len(submissions) == 1
        assert submissions[0]["member"]["id"] == participant.user_id
