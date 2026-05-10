"""Bug #5 regression: team locking policy must block member changes.

When a task has teamLockingPolicy=LOCK_ON_APPROVAL and a team's participation
is APPROVED, the team's membership roster must be frozen: adding, removing,
or accepting new members should be rejected.

NT enforces this via TeamService.checkTeamLockingStatus called from
removeTeamMember, acceptInvitation, and approveJoinRequest.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, unique_int


class TestBug5TeamLockingPolicy:
    @pytest.fixture
    def setup(self, user_client: UserCreator, api_client: TestClient) -> dict:
        owner = user_client.create_user()
        owner.token = user_client.login(api_client, owner.username, owner.password)

        member = user_client.create_user()
        member.token = user_client.login(api_client, member.username, member.password)

        outsider = user_client.create_user()
        outsider.token = user_client.login(api_client, outsider.username, outsider.password)

        suffix = unique_int()

        # Create space
        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Bug5 Space ({suffix})",
                "intro": "Test",
                "description": "Desc",
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert space_resp.status_code == 201
        space = space_resp.json()["data"]["space"]

        # Create team
        team_resp = api_client.post(
            "/teams",
            json={
                "name": f"Bug5 Team ({suffix})",
                "intro": "test",
                "description": "desc",
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert team_resp.status_code == 201
        team_id = team_resp.json()["data"]["team"]["id"]

        # Add member
        add_resp = api_client.post(
            f"/teams/{team_id}/members",
            json={"userId": member.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert add_resp.status_code == 201

        # Create task with LOCK_ON_APPROVAL
        deadline_ms = int((datetime.now(UTC).timestamp() + 7 * 86400) * 1000)
        task_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Bug5 Task ({suffix})",
                "intro": "test",
                "description": '{"type":"doc","content":[]}',
                "space": space["id"],
                "categoryId": space["defaultCategoryId"],
                "submitterType": "TEAM",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
                "deadline": deadline_ms,
                "teamLockingPolicy": "LOCK_ON_APPROVAL",
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

        # Join task as team
        join_resp = api_client.post(
            f"/tasks/{task_id}/participations/team",
            json={"teamId": team_id},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert join_resp.status_code in [200, 201], f"Join failed: {join_resp.text}"

        # Approve the team participation
        # Get the membership ID
        parts_resp = api_client.get(
            f"/tasks/{task_id}/participants",
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert parts_resp.status_code == 200
        participants = parts_resp.json()["data"]["participants"]
        membership = next(
            (p for p in participants if p["memberId"] == team_id),
            None,
        )
        assert membership is not None, "Team membership not found"
        membership_id = membership["id"]

        # Approve the membership
        approve_resp = api_client.patch(
            f"/tasks/{task_id}/participants/{membership_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert approve_resp.status_code == 200

        return {
            "owner": owner,
            "member": member,
            "outsider": outsider,
            "team_id": team_id,
            "task_id": task_id,
        }

    def test_cannot_remove_member_when_locked(
        self, api_client: TestClient, setup: dict
    ) -> None:
        """Removing a team member should fail when team is locked."""
        resp = api_client.delete(
            f"/teams/{setup['team_id']}/members/{setup['member'].user_id}",
            headers={"Authorization": f"Bearer {setup['owner'].token}"},
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
        assert "locked" in resp.json().get("message", "").lower() or \
               "locked" in resp.text.lower()

    def test_cannot_add_member_when_locked(
        self, api_client: TestClient, setup: dict
    ) -> None:
        """Adding a new member should fail when team is locked."""
        resp = api_client.post(
            f"/teams/{setup['team_id']}/members",
            json={"userId": setup["outsider"].user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {setup['owner'].token}"},
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"

    def test_no_lock_policy_allows_member_changes(
        self, user_client: UserCreator, api_client: TestClient
    ) -> None:
        """With NO_LOCK policy, member changes should still work even after approval."""
        owner = user_client.create_user()
        owner.token = user_client.login(api_client, owner.username, owner.password)
        member = user_client.create_user()
        member.token = user_client.login(api_client, member.username, member.password)
        extra = user_client.create_user()
        extra.token = user_client.login(api_client, extra.username, extra.password)

        suffix = unique_int()

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Bug5 NoLock Space ({suffix})",
                "intro": "Test",
                "description": "Desc",
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        space = space_resp.json()["data"]["space"]

        team_resp = api_client.post(
            "/teams",
            json={
                "name": f"Bug5 NoLock Team ({suffix})",
                "intro": "test",
                "description": "desc",
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        team_id = team_resp.json()["data"]["team"]["id"]

        api_client.post(
            f"/teams/{team_id}/members",
            json={"userId": member.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )

        deadline_ms = int((datetime.now(UTC).timestamp() + 7 * 86400) * 1000)
        task_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Bug5 NoLock Task ({suffix})",
                "intro": "test",
                "description": '{"type":"doc","content":[]}',
                "space": space["id"],
                "categoryId": space["defaultCategoryId"],
                "submitterType": "TEAM",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
                "deadline": deadline_ms,
                "teamLockingPolicy": "NO_LOCK",
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        task_id = task_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )

        join_resp = api_client.post(
            f"/tasks/{task_id}/participations/team",
            json={"teamId": team_id},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert join_resp.status_code in [200, 201]

        parts = api_client.get(
            f"/tasks/{task_id}/participants",
            headers={"Authorization": f"Bearer {owner.token}"},
        ).json()["data"]["participants"]
        mid = next(p for p in parts if p["memberId"] == team_id)["id"]
        api_client.patch(
            f"/tasks/{task_id}/participants/{mid}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )

        # With NO_LOCK, adding a member should succeed
        add_resp = api_client.post(
            f"/teams/{team_id}/members",
            json={"userId": extra.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert add_resp.status_code == 201, f"Expected 201, got {add_resp.status_code}"
