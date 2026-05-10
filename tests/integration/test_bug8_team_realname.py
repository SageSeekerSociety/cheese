"""Bug #8 regression: team real-name check must verify ALL members.

When a task has requireRealName=true and submitterType=TEAM, the eligibility
check was only verifying the requesting user's real-name status. NT checks
all team members via getTeamMembers(teamId, queryRealNameStatus=true).

After the fix, if even one team member lacks real-name info, the eligibility
response includes TEAM_MEMBER_MISSING_REAL_NAME.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.conftest import UserCreator, unique_int


class TestBug8TeamRealNameCheck:
    @pytest.fixture
    def setup(
        self,
        user_client: UserCreator,
        api_client: TestClient,
        db_session: AsyncSession,
        _portal,
    ) -> dict:
        owner = user_client.create_user()
        owner.token = user_client.login(api_client, owner.username, owner.password)

        member = user_client.create_user()
        member.token = user_client.login(api_client, member.username, member.password)

        suffix = unique_int()

        # Create space
        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Bug8 Space ({suffix})",
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
                "name": f"Bug8 Team ({suffix})",
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

        # Create task with requireRealName
        task_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Bug8 Task ({suffix})",
                "intro": "test",
                "description": '{"type":"doc","content":[]}',
                "space": space["id"],
                "categoryId": space["defaultCategoryId"],
                "submitterType": "TEAM",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
                "deadline": int((datetime.now(UTC).timestamp() + 7 * 86400) * 1000),
                "requireRealName": True,
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
            "db_session": db_session,
            "portal": _portal,
        }

    def _add_real_name(self, portal, db_session, user_id: int) -> None:
        """Insert a real-name identity record for a user."""
        from app.domain.user.models import UserRealNameIdentity

        async def _insert():
            now = datetime.now(UTC)
            identity = UserRealNameIdentity(
                user_id=user_id,
                encrypted=False,
                real_name="Test Name",
                student_id="2024000001",
                grade="2024",
                major="CS",
                class_name="Class 1",
                created_at=now,
                updated_at=now,
            )
            db_session.add(identity)
            await db_session.flush()

        portal.call(_insert)

    def test_no_realname_shows_missing_reason(
        self, api_client: TestClient, setup: dict
    ) -> None:
        """Without any real-name info, TEAM_MEMBER_MISSING_REAL_NAME appears."""
        resp = api_client.get(
            f"/tasks/{setup['task_id']}",
            params={"queryJoinability": "true"},
            headers={"Authorization": f"Bearer {setup['owner'].token}"},
        )
        assert resp.status_code == 200
        teams = resp.json()["data"]["task"]["participationEligibility"]["teams"]
        team_entry = next(
            (t for t in teams if t["team"]["id"] == setup["team_id"]), None
        )
        assert team_entry is not None
        codes = [r["code"] for r in team_entry["eligibility"]["reasons"]]
        assert "TEAM_MEMBER_MISSING_REAL_NAME" in codes

    def test_owner_verified_but_member_not_still_shows_missing(
        self, api_client: TestClient, setup: dict
    ) -> None:
        """Owner has real-name but member doesn't: still shows missing.

        Before the fix, only the requesting user (owner) was checked,
        so this would incorrectly show no TEAM_MEMBER_MISSING_REAL_NAME.
        """
        self._add_real_name(setup["portal"], setup["db_session"], setup["owner"].user_id)

        resp = api_client.get(
            f"/tasks/{setup['task_id']}",
            params={"queryJoinability": "true"},
            headers={"Authorization": f"Bearer {setup['owner'].token}"},
        )
        assert resp.status_code == 200
        teams = resp.json()["data"]["task"]["participationEligibility"]["teams"]
        team_entry = next(
            (t for t in teams if t["team"]["id"] == setup["team_id"]), None
        )
        assert team_entry is not None
        codes = [r["code"] for r in team_entry["eligibility"]["reasons"]]
        # Must still report missing because the MEMBER hasn't verified
        assert "TEAM_MEMBER_MISSING_REAL_NAME" in codes

    def test_all_members_verified_no_missing_reason(
        self, api_client: TestClient, setup: dict
    ) -> None:
        """When all team members have real-name info, no MISSING reason."""
        self._add_real_name(setup["portal"], setup["db_session"], setup["owner"].user_id)
        self._add_real_name(setup["portal"], setup["db_session"], setup["member"].user_id)

        resp = api_client.get(
            f"/tasks/{setup['task_id']}",
            params={"queryJoinability": "true"},
            headers={"Authorization": f"Bearer {setup['owner'].token}"},
        )
        assert resp.status_code == 200
        teams = resp.json()["data"]["task"]["participationEligibility"]["teams"]
        team_entry = next(
            (t for t in teams if t["team"]["id"] == setup["team_id"]), None
        )
        assert team_entry is not None
        codes = [r["code"] for r in team_entry["eligibility"]["reasons"]]
        assert "TEAM_MEMBER_MISSING_REAL_NAME" not in codes
