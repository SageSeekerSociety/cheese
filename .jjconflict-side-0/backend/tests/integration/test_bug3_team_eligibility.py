"""Bug #3 regression: team task eligibility only lists OWNER/ADMIN teams.

NT TeamRepository.getTeamsThatUserCanUseToJoinTask filters by OWNER/ADMIN role.
This is by design -- ordinary MEMBER role cannot represent a team in a task.
This test confirms the behavior matches NT.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, unique_int


class TestBug3TeamEligibilityByDesign:
    @pytest.fixture
    def setup(self, user_client: UserCreator, api_client: TestClient) -> dict:
        owner = user_client.create_user()
        owner.token = user_client.login(api_client, owner.username, owner.password)

        member = user_client.create_user()
        member.token = user_client.login(api_client, member.username, member.password)

        suffix = unique_int()

        # Create space
        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Bug3 Space ({suffix})",
                "intro": "Test",
                "description": "Desc",
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert space_resp.status_code == 201
        space = space_resp.json()["data"]["space"]

        # Create team (owner is OWNER)
        team_resp = api_client.post(
            "/teams",
            json={
                "name": f"Bug3 Team ({suffix})",
                "intro": "test",
                "description": "desc",
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert team_resp.status_code == 201
        team_id = team_resp.json()["data"]["team"]["id"]

        # Add member as MEMBER role
        add_resp = api_client.post(
            f"/teams/{team_id}/members",
            json={"userId": member.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert add_resp.status_code == 201

        # Create team task
        task_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Bug3 Task ({suffix})",
                "intro": "test",
                "description": '{"type":"doc","content":[]}',
                "space": space["id"],
                "categoryId": space["defaultCategoryId"],
                "submitterType": "TEAM",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
                "deadline": int((datetime.now(UTC).timestamp() + 7 * 86400) * 1000),
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert task_resp.status_code == 200
        task_id = task_resp.json()["data"]["task"]["id"]

        # Approve task
        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )

        return {
            "owner": owner,
            "member": member,
            "team_id": team_id,
            "task_id": task_id,
        }

    def test_owner_sees_team_in_eligibility(
        self, api_client: TestClient, setup: dict
    ) -> None:
        """OWNER should see the team in eligibility list."""
        resp = api_client.get(
            f"/tasks/{setup['task_id']}",
            params={"queryJoinability": "true"},
            headers={"Authorization": f"Bearer {setup['owner'].token}"},
        )
        assert resp.status_code == 200
        eligibility = resp.json()["data"]["task"]["participationEligibility"]
        teams = eligibility["teams"]
        assert isinstance(teams, list)
        team_ids = [t["team"]["id"] for t in teams]
        assert setup["team_id"] in team_ids

    def test_member_does_not_see_team_in_eligibility(
        self, api_client: TestClient, setup: dict
    ) -> None:
        """Ordinary MEMBER should NOT see the team in eligibility.

        This matches NT behavior: getTeamsThatUserCanUseToJoinTask only
        returns teams where user is OWNER or ADMIN.
        """
        resp = api_client.get(
            f"/tasks/{setup['task_id']}",
            params={"queryJoinability": "true"},
            headers={"Authorization": f"Bearer {setup['member'].token}"},
        )
        assert resp.status_code == 200
        eligibility = resp.json()["data"]["task"]["participationEligibility"]
        teams = eligibility["teams"]
        assert isinstance(teams, list)
        team_ids = [t["team"]["id"] for t in teams]
        assert setup["team_id"] not in team_ids
