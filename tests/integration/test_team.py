import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, unique_int


class TestTeamIntegration:
    @pytest.fixture
    def team_setup(self, user_client: UserCreator, api_client: TestClient) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        admin = user_client.create_user()
        admin.token = user_client.login(api_client, admin.username, admin.password)

        member = user_client.create_user()
        member.token = user_client.login(api_client, member.username, member.password)

        another_user = user_client.create_user()
        another_user.token = user_client.login(
            api_client, another_user.username, another_user.password
        )

        suffix = unique_int(10000000, 99999999)
        return {
            "creator": creator,
            "admin": admin,
            "member": member,
            "another_user": another_user,
            "team_name": f"Test Team ({suffix})",
            "team_intro": "This is a test team",
            "team_description": "A lengthy description. " * 20,
            "team_avatar_id": 1,
        }

    def test_create_team(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        response = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers=headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["code"] == 201
        assert data["data"]["team"]["name"] == team_setup["team_name"]
        assert data["data"]["team"]["intro"] == team_setup["team_intro"]
        assert data["data"]["team"]["id"] > 0
        assert data["data"]["team"]["owner"]["id"] == creator.user_id

    def test_get_team(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers=headers,
        )
        assert create_resp.status_code == 201
        team_id = create_resp.json()["data"]["team"]["id"]

        get_resp = api_client.get(f"/teams/{team_id}", headers=headers)
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["data"]["team"]["id"] == team_id
        assert data["data"]["team"]["name"] == team_setup["team_name"]
        assert data["data"]["team"]["owner"]["id"] == creator.user_id
        assert data["data"]["team"]["admins"]["total"] == 0
        assert data["data"]["team"]["members"]["total"] == 0

    def test_enumerate_teams(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers=headers,
        )
        assert create_resp.status_code == 201

        resp = api_client.get(
            "/teams",
            params={"query": team_setup["team_name"], "pageSize": 10},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]["teams"]) >= 1

    def test_get_my_teams(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers=headers,
        )
        assert create_resp.status_code == 201
        team_id = create_resp.json()["data"]["team"]["id"]

        resp = api_client.get("/teams/my-teams", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        team_ids = [t["id"] for t in data["data"]["teams"]]
        assert team_id in team_ids
        my_team = next((t for t in data["data"]["teams"] if t["id"] == team_id), None)
        assert my_team is not None
        assert my_team["owner"]["id"] == creator.user_id

    def test_update_team(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers=headers,
        )
        assert create_resp.status_code == 201
        team_id = create_resp.json()["data"]["team"]["id"]

        updated_name = f"{team_setup['team_name']} (Updated)"
        patch_resp = api_client.patch(
            f"/teams/{team_id}",
            json={"name": updated_name},
            headers=headers,
        )
        assert patch_resp.status_code == 200
        data = patch_resp.json()
        assert data["data"]["team"]["name"] == updated_name

    def test_update_team_forbidden_for_non_member(
        self, api_client: TestClient, team_setup: dict
    ) -> None:
        creator = team_setup["creator"]
        another_user = team_setup["another_user"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 201
        team_id = create_resp.json()["data"]["team"]["id"]

        patch_resp = api_client.patch(
            f"/teams/{team_id}",
            json={"name": "Attempted Unauthorized Update"},
            headers={"Authorization": f"Bearer {another_user.token}"},
        )
        assert patch_resp.status_code == 403

    def test_get_team_members(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers=headers,
        )
        assert create_resp.status_code == 201
        team_id = create_resp.json()["data"]["team"]["id"]

        members_resp = api_client.get(f"/teams/{team_id}/members", headers=headers)
        assert members_resp.status_code == 200
        data = members_resp.json()
        assert len(data["data"]["members"]) == 1
        assert data["data"]["members"][0]["role"] == "OWNER"
        assert data["data"]["members"][0]["userId"] == creator.user_id

    def test_invite_and_accept_member(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        member = team_setup["member"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 201
        team_id = create_resp.json()["data"]["team"]["id"]

        invite_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": member.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert invite_resp.status_code == 201
        invitation_id = invite_resp.json()["data"]["invitation"]["id"]

        accept_resp = api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {member.token}"},
        )
        assert accept_resp.status_code == 204

        members_resp = api_client.get(
            f"/teams/{team_id}/members",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert members_resp.status_code == 200
        data = members_resp.json()
        user_ids = [m["userId"] for m in data["data"]["members"]]
        assert member.user_id in user_ids

        team_resp = api_client.get(
            f"/teams/{team_id}", headers={"Authorization": f"Bearer {creator.token}"}
        )
        assert team_resp.status_code == 200
        team_data = team_resp.json()["data"]["team"]
        assert team_data["owner"]["id"] == creator.user_id
        assert team_data["admins"]["total"] == 0
        assert team_data["members"]["total"] == 1

    def test_invite_admin(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        admin = team_setup["admin"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 201
        team_id = create_resp.json()["data"]["team"]["id"]

        invite_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": admin.user_id, "role": "ADMIN"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert invite_resp.status_code == 201
        invitation_id = invite_resp.json()["data"]["invitation"]["id"]

        accept_resp = api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {admin.token}"},
        )
        assert accept_resp.status_code == 204

        members_resp = api_client.get(
            f"/teams/{team_id}/members",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert members_resp.status_code == 200
        data = members_resp.json()
        admin_member = next(
            (m for m in data["data"]["members"] if m["userId"] == admin.user_id), None
        )
        assert admin_member is not None
        assert admin_member["role"] == "ADMIN"

        team_resp = api_client.get(
            f"/teams/{team_id}", headers={"Authorization": f"Bearer {creator.token}"}
        )
        assert team_resp.status_code == 200
        team_data = team_resp.json()["data"]["team"]
        assert team_data["owner"]["id"] == creator.user_id
        assert team_data["admins"]["total"] == 1
        assert team_data["members"]["total"] == 0

    def test_member_cannot_invite(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        member = team_setup["member"]
        another_user = team_setup["another_user"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        invite_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": member.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        invitation_id = invite_resp.json()["data"]["invitation"]["id"]
        api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {member.token}"},
        )

        invite_resp2 = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": another_user.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {member.token}"},
        )
        assert invite_resp2.status_code == 403

    def test_change_member_role(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        member = team_setup["member"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        invite_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": member.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        invitation_id = invite_resp.json()["data"]["invitation"]["id"]
        api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {member.token}"},
        )

        patch_resp = api_client.patch(
            f"/teams/{team_id}/members/{member.user_id}",
            json={"role": "ADMIN"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert patch_resp.status_code == 200

        members_resp = api_client.get(
            f"/teams/{team_id}/members",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        member_data = next(
            (m for m in members_resp.json()["data"]["members"] if m["userId"] == member.user_id),
            None,
        )
        assert member_data["role"] == "ADMIN"

    def test_remove_member(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        member = team_setup["member"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        invite_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": member.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        invitation_id = invite_resp.json()["data"]["invitation"]["id"]
        api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {member.token}"},
        )

        remove_resp = api_client.delete(
            f"/teams/{team_id}/members/{member.user_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert remove_resp.status_code == 204

        members_resp = api_client.get(
            f"/teams/{team_id}/members",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        user_ids = [m["userId"] for m in members_resp.json()["data"]["members"]]
        assert member.user_id not in user_ids

    def test_delete_team(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers=headers,
        )
        assert create_resp.status_code == 201
        team_id = create_resp.json()["data"]["team"]["id"]

        delete_resp = api_client.delete(f"/teams/{team_id}", headers=headers)
        assert delete_resp.status_code == 204

        get_resp = api_client.get(f"/teams/{team_id}", headers=headers)
        assert get_resp.status_code == 404

    def test_join_request_workflow(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        member = team_setup["member"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        request_resp = api_client.post(
            f"/teams/{team_id}/join-requests",
            json={"message": "Please let me join!"},
            headers={"Authorization": f"Bearer {member.token}"},
        )
        assert request_resp.status_code == 201
        request_id = request_resp.json()["data"]["application"]["id"]

        list_resp = api_client.get(
            f"/teams/{team_id}/join-requests",
            params={"status": "PENDING"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert list_resp.status_code == 200
        request_ids = [r["id"] for r in list_resp.json()["data"]["applications"]]
        assert request_id in request_ids

        approve_resp = api_client.post(
            f"/teams/{team_id}/join-requests/{request_id}/approve",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert approve_resp.status_code == 204

        members_resp = api_client.get(
            f"/teams/{team_id}/members",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        user_ids = [m["userId"] for m in members_resp.json()["data"]["members"]]
        assert member.user_id in user_ids

    def test_enumerate_teams_by_id(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers=headers,
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        resp = api_client.get(
            "/teams",
            params={"query": str(team_id), "pageSize": 1},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]["teams"]) >= 1

    def test_enumerate_teams_with_pagination(
        self, api_client: TestClient, team_setup: dict
    ) -> None:
        creator = team_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers=headers,
        )
        assert create_resp.status_code == 201

        resp = api_client.get(
            "/teams",
            params={"query": team_setup["team_name"], "pageSize": 1},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "page" in data["data"]
        assert data["data"]["page"]["pageSize"] == 1

    def test_enumerate_teams_no_filter(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        resp = api_client.get("/teams", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "teams" in data["data"]

    def test_update_team_forbidden_for_member(
        self, api_client: TestClient, team_setup: dict
    ) -> None:
        creator = team_setup["creator"]
        member = team_setup["member"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        invite_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": member.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        invitation_id = invite_resp.json()["data"]["invitation"]["id"]
        api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {member.token}"},
        )

        patch_resp = api_client.patch(
            f"/teams/{team_id}",
            json={"name": "Attempted Update by Member"},
            headers={"Authorization": f"Bearer {member.token}"},
        )
        assert patch_resp.status_code == 403

    def test_update_team_success_for_admin(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        admin = team_setup["admin"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        invite_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": admin.user_id, "role": "ADMIN"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        invitation_id = invite_resp.json()["data"]["invitation"]["id"]
        api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {admin.token}"},
        )

        updated_name = f"{team_setup['team_name']} (Updated by Admin)"
        patch_resp = api_client.patch(
            f"/teams/{team_id}",
            json={"name": updated_name},
            headers={"Authorization": f"Bearer {admin.token}"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["data"]["team"]["name"] == updated_name

    def test_get_team_as_member(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        member = team_setup["member"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        invite_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": member.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        invitation_id = invite_resp.json()["data"]["invitation"]["id"]
        api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {member.token}"},
        )

        resp = api_client.get(
            f"/teams/{team_id}",
            headers={"Authorization": f"Bearer {member.token}"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]["team"]
        assert data["joined"] is True
        assert data["role"] == "MEMBER"

    def test_get_team_as_non_member(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        another_user = team_setup["another_user"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        resp = api_client.get(
            f"/teams/{team_id}",
            headers={"Authorization": f"Bearer {another_user.token}"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]["team"]
        assert data["joined"] is False
        assert data.get("role") is None

    def test_remove_self_as_member(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        member = team_setup["member"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        invite_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": member.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        invitation_id = invite_resp.json()["data"]["invitation"]["id"]
        api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {member.token}"},
        )

        remove_resp = api_client.delete(
            f"/teams/{team_id}/members/{member.user_id}",
            headers={"Authorization": f"Bearer {member.token}"},
        )
        assert remove_resp.status_code == 204

        members_resp = api_client.get(
            f"/teams/{team_id}/members",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        user_ids = [m["userId"] for m in members_resp.json()["data"]["members"]]
        assert member.user_id not in user_ids

    def test_admin_can_invite_member(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        admin = team_setup["admin"]
        member = team_setup["member"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        invite_admin_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": admin.user_id, "role": "ADMIN"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        admin_invite_id = invite_admin_resp.json()["data"]["invitation"]["id"]
        api_client.post(
            f"/users/me/team-invitations/{admin_invite_id}/accept",
            headers={"Authorization": f"Bearer {admin.token}"},
        )

        invite_member_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": member.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {admin.token}"},
        )
        assert invite_member_resp.status_code == 201

    def test_change_role_admin_to_member(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        admin = team_setup["admin"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        invite_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": admin.user_id, "role": "ADMIN"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        invitation_id = invite_resp.json()["data"]["invitation"]["id"]
        api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {admin.token}"},
        )

        patch_resp = api_client.patch(
            f"/teams/{team_id}/members/{admin.user_id}",
            json={"role": "MEMBER"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert patch_resp.status_code == 200

        members_resp = api_client.get(
            f"/teams/{team_id}/members",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        admin_member = next(
            (m for m in members_resp.json()["data"]["members"] if m["userId"] == admin.user_id),
            None,
        )
        assert admin_member["role"] == "MEMBER"

    def test_change_role_member_to_admin(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        member = team_setup["member"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        invite_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": member.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        invitation_id = invite_resp.json()["data"]["invitation"]["id"]
        api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {member.token}"},
        )

        patch_resp = api_client.patch(
            f"/teams/{team_id}/members/{member.user_id}",
            json={"role": "ADMIN"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert patch_resp.status_code == 200

        patch_back_resp = api_client.patch(
            f"/teams/{team_id}/members/{member.user_id}",
            json={"role": "MEMBER"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert patch_back_resp.status_code == 200

        members_resp = api_client.get(
            f"/teams/{team_id}/members",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        member_data = next(
            (m for m in members_resp.json()["data"]["members"] if m["userId"] == member.user_id),
            None,
        )
        assert member_data["role"] == "MEMBER"

    def test_add_member_back_via_invitation(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        member = team_setup["member"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        invite_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": member.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        invitation_id = invite_resp.json()["data"]["invitation"]["id"]
        api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {member.token}"},
        )

        api_client.delete(
            f"/teams/{team_id}/members/{member.user_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        invite_again_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": member.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert invite_again_resp.status_code == 201
        new_invitation_id = invite_again_resp.json()["data"]["invitation"]["id"]

        api_client.post(
            f"/users/me/team-invitations/{new_invitation_id}/accept",
            headers={"Authorization": f"Bearer {member.token}"},
        )

        members_resp = api_client.get(
            f"/teams/{team_id}/members",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        user_ids = [m["userId"] for m in members_resp.json()["data"]["members"]]
        assert member.user_id in user_ids

    def test_get_team_details_as_owner(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers=headers,
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        get_resp = api_client.get(f"/teams/{team_id}", headers=headers)
        assert get_resp.status_code == 200
        data = get_resp.json()["data"]["team"]
        assert data["id"] == team_id
        assert data["name"] == team_setup["team_name"]
        assert data["intro"] == team_setup["team_intro"]
        assert data["avatarId"] == team_setup["team_avatar_id"]
        assert data["owner"]["id"] == creator.user_id
        assert data["admins"]["total"] == 0
        assert data["members"]["total"] == 0
        assert data["joined"] is True
        assert data["role"] == "OWNER"

    def test_update_team_fails_for_anonymous(
        self, api_client: TestClient, team_setup: dict
    ) -> None:
        creator = team_setup["creator"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        resp = api_client.patch(
            f"/teams/{team_id}",
            json={"name": "Anonymous Update Attempt"},
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"

    def test_remove_member_using_owner_token(
        self, api_client: TestClient, team_setup: dict
    ) -> None:
        creator = team_setup["creator"]
        member = team_setup["member"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        invite_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": member.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        invitation_id = invite_resp.json()["data"]["invitation"]["id"]
        api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {member.token}"},
        )

        remove_resp = api_client.delete(
            f"/teams/{team_id}/members/{member.user_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert remove_resp.status_code == 204, f"Expected 204, got {remove_resp.status_code}"

        members_resp = api_client.get(
            f"/teams/{team_id}/members",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        user_ids = [m["userId"] for m in members_resp.json()["data"]["members"]]
        assert member.user_id not in user_ids

    def test_remove_admin_using_owner_token(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        admin = team_setup["admin"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        invite_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": admin.user_id, "role": "ADMIN"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        invitation_id = invite_resp.json()["data"]["invitation"]["id"]
        api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {admin.token}"},
        )

        remove_resp = api_client.delete(
            f"/teams/{team_id}/members/{admin.user_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert remove_resp.status_code == 204, f"Expected 204, got {remove_resp.status_code}"

        members_resp = api_client.get(
            f"/teams/{team_id}/members",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        user_ids = [m["userId"] for m in members_resp.json()["data"]["members"]]
        assert admin.user_id not in user_ids

    def test_add_already_member_fails(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        member = team_setup["member"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        invite_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": member.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        invitation_id = invite_resp.json()["data"]["invitation"]["id"]
        api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {member.token}"},
        )

        members_resp = api_client.get(
            f"/teams/{team_id}/members",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        member_data = next(
            (m for m in members_resp.json()["data"]["members"] if m["userId"] == member.user_id),
            None,
        )
        assert member_data is not None
        assert member_data["role"] == "MEMBER"

        invite_again_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": member.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert invite_again_resp.status_code in (
            400,
            409,
        ), f"Expected 400 or 409 when inviting already-member, got {invite_again_resp.status_code}"

    def test_change_role_multiple_times(self, api_client: TestClient, team_setup: dict) -> None:
        creator = team_setup["creator"]
        member = team_setup["member"]

        create_resp = api_client.post(
            "/teams",
            json={
                "name": team_setup["team_name"],
                "intro": team_setup["team_intro"],
                "description": team_setup["team_description"],
                "avatarId": team_setup["team_avatar_id"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = create_resp.json()["data"]["team"]["id"]

        invite_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": member.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        invitation_id = invite_resp.json()["data"]["invitation"]["id"]
        api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {member.token}"},
        )

        patch_resp = api_client.patch(
            f"/teams/{team_id}/members/{member.user_id}",
            json={"role": "ADMIN"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert patch_resp.status_code == 200

        members_resp = api_client.get(
            f"/teams/{team_id}/members",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        member_data = next(
            (m for m in members_resp.json()["data"]["members"] if m["userId"] == member.user_id),
            None,
        )
        assert member_data["role"] == "ADMIN"

        patch_resp2 = api_client.patch(
            f"/teams/{team_id}/members/{member.user_id}",
            json={"role": "MEMBER"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert patch_resp2.status_code == 200

        members_resp2 = api_client.get(
            f"/teams/{team_id}/members",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        member_data2 = next(
            (m for m in members_resp2.json()["data"]["members"] if m["userId"] == member.user_id),
            None,
        )
        assert member_data2["role"] == "MEMBER"

        owner_data = next(
            (m for m in members_resp2.json()["data"]["members"] if m["userId"] == creator.user_id),
            None,
        )
        assert owner_data["role"] == "OWNER"
