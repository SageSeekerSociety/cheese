"""Bug #7 regression: task participant user info must include full User shape.

GET /tasks/{taskId}/participants returned { participant: { id: N } } without
username/nickname/avatarId, causing the frontend to display "unknown user".
The fix bulk-fetches User+UserProfile and returns the full User shape.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, unique_int


class TestBug7ParticipantUserInfo:
    @pytest.fixture
    def setup(self, user_client: UserCreator, api_client: TestClient) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(
            api_client, creator.username, creator.password
        )

        participant = user_client.create_user()
        participant.token = user_client.login(
            api_client, participant.username, participant.password
        )

        suffix = unique_int()

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Bug7 Space ({suffix})",
                "intro": "Test",
                "description": "Desc",
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert space_resp.status_code == 201
        space = space_resp.json()["data"]["space"]

        task_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Bug7 Task ({suffix})",
                "intro": "test",
                "description": '{"type":"doc","content":[]}',
                "space": space["id"],
                "categoryId": space["defaultCategoryId"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
                "deadline": int((datetime.now(UTC).timestamp() + 7 * 86400) * 1000),
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert task_resp.status_code == 200
        task_id = task_resp.json()["data"]["task"]["id"]

        # Approve task
        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        # Participant joins task
        join_resp = api_client.post(
            f"/tasks/{task_id}/participants",
            json={"role": "PARTICIPANT"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert join_resp.status_code in [200, 201], f"Join failed: {join_resp.text}"

        return {
            "creator": creator,
            "participant": participant,
            "task_id": task_id,
        }

    def test_participants_list_has_full_user_info(
        self, api_client: TestClient, setup: dict
    ) -> None:
        """Each participant must have username, nickname, avatarId."""
        resp = api_client.get(
            f"/tasks/{setup['task_id']}/participants",
            headers={"Authorization": f"Bearer {setup['creator'].token}"},
        )
        assert resp.status_code == 200
        participants = resp.json()["data"]["participants"]
        assert len(participants) >= 1

        # Find our participant
        p = next(
            (p for p in participants if p["memberId"] == setup["participant"].user_id),
            None,
        )
        assert p is not None, "Participant not found in list"

        # participant field must contain full user info, not just { id: N }
        pi = p["participant"]
        assert "username" in pi, "Missing username in participant info"
        assert "nickname" in pi, "Missing nickname in participant info"
        assert "avatarId" in pi, "Missing avatarId in participant info"
        assert pi["username"] == setup["participant"].username
        assert p["member"]["name"] == pi["nickname"]

    def test_single_participant_has_full_user_info(
        self, api_client: TestClient, setup: dict
    ) -> None:
        """GET /tasks/{id}/participants/{pid} must also return full user info."""
        # First, get the participant id from the list
        list_resp = api_client.get(
            f"/tasks/{setup['task_id']}/participants",
            headers={"Authorization": f"Bearer {setup['creator'].token}"},
        )
        participants = list_resp.json()["data"]["participants"]
        p = next(
            (p for p in participants if p["memberId"] == setup["participant"].user_id),
            None,
        )
        assert p is not None
        pid = p["id"]

        resp = api_client.get(
            f"/tasks/{setup['task_id']}/participants/{pid}",
            headers={"Authorization": f"Bearer {setup['creator'].token}"},
        )
        assert resp.status_code == 200
        pi = resp.json()["data"]["participant"]["participant"]
        assert "username" in pi
        assert "nickname" in pi
        assert pi["username"] == setup["participant"].username
