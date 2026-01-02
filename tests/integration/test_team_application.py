from __future__ import annotations

import random

import httpx
import pytest

from tests.integration.conftest import UserCreator


class TestTeamApplicationIntegration:
    @pytest.fixture
    def setup_team_application(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        owner = user_client.create_user()
        owner.token = user_client.login(api_client, owner.username, owner.password)

        requester = user_client.create_user()
        requester.token = user_client.login(api_client, requester.username, requester.password)

        invitee = user_client.create_user()
        invitee.token = user_client.login(api_client, invitee.username, invitee.password)

        suffix = random.randint(10000000, 99999999)

        team_resp = api_client.post(
            "/teams",
            json={
                "name": f"Application Test Team ({suffix})",
                "intro": "Team for testing applications",
                "description": "A lengthy description. " * 10,
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert team_resp.status_code == 201, f"Team creation failed: {team_resp.text}"
        team_id = team_resp.json()["data"]["team"]["id"]

        return {
            "owner": owner,
            "requester": requester,
            "invitee": invitee,
            "team_id": team_id,
        }

    def test_request_to_join_team(self, setup_team_application: dict, api_client: httpx.Client):
        requester = setup_team_application["requester"]
        team_id = setup_team_application["team_id"]

        resp = api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "Please let me join!"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "application" in data
        assert data["application"]["id"] > 0

    def test_list_pending_requests_for_user(self, setup_team_application: dict, api_client: httpx.Client):
        requester = setup_team_application["requester"]
        team_id = setup_team_application["team_id"]

        api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "Join request"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )

        resp = api_client.get(
            "/users/me/team-requests",
            params={"status": "PENDING"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "requests" in data
        assert isinstance(data["requests"], list)

    def test_list_pending_requests_for_team(self, setup_team_application: dict, api_client: httpx.Client):
        owner = setup_team_application["owner"]
        requester = setup_team_application["requester"]
        team_id = setup_team_application["team_id"]

        api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "Join request"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )

        resp = api_client.get(
            f"/teams/{team_id}/requests",
            params={"status": "PENDING"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "applications" in data

    def test_approve_join_request(self, setup_team_application: dict, api_client: httpx.Client):
        owner = setup_team_application["owner"]
        requester = setup_team_application["requester"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "Join request"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        request_id = create_resp.json()["data"]["application"]["id"]

        resp = api_client.post(
            f"/teams/{team_id}/requests/{request_id}/approve",
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"

    def test_reject_join_request(self, setup_team_application: dict, api_client: httpx.Client):
        owner = setup_team_application["owner"]
        requester = setup_team_application["requester"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "Another join request"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        request_id = create_resp.json()["data"]["application"]["id"]

        resp = api_client.post(
            f"/teams/{team_id}/requests/{request_id}/reject",
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"

    def test_cancel_join_request(self, setup_team_application: dict, api_client: httpx.Client):
        requester = setup_team_application["requester"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "Request to cancel"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        request_id = create_resp.json()["data"]["application"]["id"]

        resp = api_client.delete(
            f"/users/me/team-requests/{request_id}",
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"

    def test_invite_user_to_team(self, setup_team_application: dict, api_client: httpx.Client):
        owner = setup_team_application["owner"]
        invitee = setup_team_application["invitee"]
        team_id = setup_team_application["team_id"]

        resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": invitee.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "invitation" in data
        assert data["invitation"]["id"] > 0

    def test_list_invitations_for_user(self, setup_team_application: dict, api_client: httpx.Client):
        owner = setup_team_application["owner"]
        invitee = setup_team_application["invitee"]
        team_id = setup_team_application["team_id"]

        api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": invitee.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )

        resp = api_client.get(
            "/users/me/team-invitations",
            params={"status": "PENDING"},
            headers={"Authorization": f"Bearer {invitee.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "invitations" in data

    def test_accept_invitation(self, setup_team_application: dict, api_client: httpx.Client):
        owner = setup_team_application["owner"]
        invitee = setup_team_application["invitee"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": invitee.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        invitation_id = create_resp.json()["data"]["invitation"]["id"]

        resp = api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {invitee.token}"},
        )
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"

    def test_decline_invitation(self, setup_team_application: dict, api_client: httpx.Client):
        owner = setup_team_application["owner"]
        invitee = setup_team_application["invitee"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": invitee.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        invitation_id = create_resp.json()["data"]["invitation"]["id"]

        resp = api_client.post(
            f"/users/me/team-invitations/{invitation_id}/decline",
            headers={"Authorization": f"Bearer {invitee.token}"},
        )
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"

    def test_cancel_invitation(self, setup_team_application: dict, api_client: httpx.Client):
        owner = setup_team_application["owner"]
        invitee = setup_team_application["invitee"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": invitee.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        invitation_id = create_resp.json()["data"]["invitation"]["id"]

        resp = api_client.delete(
            f"/teams/{team_id}/invitations/{invitation_id}",
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"

    def test_verify_membership_after_request_approval(
        self, setup_team_application: dict, api_client: httpx.Client
    ):
        owner = setup_team_application["owner"]
        requester = setup_team_application["requester"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "Join request for verification"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        request_id = create_resp.json()["data"]["application"]["id"]

        api_client.post(
            f"/teams/{team_id}/requests/{request_id}/approve",
            headers={"Authorization": f"Bearer {owner.token}"},
        )

        members_resp = api_client.get(
            f"/teams/{team_id}/members",
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert members_resp.status_code == 200
        members = members_resp.json()["data"]["members"]
        member = next((m for m in members if m["user"]["id"] == requester.user_id), None)
        assert member is not None, "Requester should be a member after approval"
        assert member["role"] == "MEMBER"

    def test_verify_membership_after_invitation_acceptance(
        self, setup_team_application: dict, api_client: httpx.Client
    ):
        owner = setup_team_application["owner"]
        invitee = setup_team_application["invitee"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": invitee.user_id, "role": "ADMIN"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        invitation_id = create_resp.json()["data"]["invitation"]["id"]

        api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {invitee.token}"},
        )

        members_resp = api_client.get(
            f"/teams/{team_id}/members",
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert members_resp.status_code == 200
        members = members_resp.json()["data"]["members"]
        member = next((m for m in members if m["user"]["id"] == invitee.user_id), None)
        assert member is not None, "Invitee should be a member after acceptance"
        assert member["role"] == "ADMIN"

    def test_list_sent_invitations_for_team(
        self, setup_team_application: dict, api_client: httpx.Client
    ):
        owner = setup_team_application["owner"]
        invitee = setup_team_application["invitee"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": invitee.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        invitation_id = create_resp.json()["data"]["invitation"]["id"]

        resp = api_client.get(
            f"/teams/{team_id}/invitations",
            params={"status": "PENDING"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "invitations" in data
        found = next((i for i in data["invitations"] if i["id"] == invitation_id), None)
        assert found is not None

    def test_check_rejected_request_status(
        self, setup_team_application: dict, api_client: httpx.Client
    ):
        owner = setup_team_application["owner"]
        requester = setup_team_application["requester"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "Request to reject"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        request_id = create_resp.json()["data"]["application"]["id"]

        api_client.post(
            f"/teams/{team_id}/requests/{request_id}/reject",
            headers={"Authorization": f"Bearer {owner.token}"},
        )

        resp = api_client.get(
            "/users/me/team-requests",
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        assert resp.status_code == 200
        requests = resp.json()["data"]["requests"]
        request = next((r for r in requests if r["id"] == request_id), None)
        assert request is not None
        assert request["status"] == "REJECTED"

    def test_check_canceled_request_status(
        self, setup_team_application: dict, api_client: httpx.Client
    ):
        requester = setup_team_application["requester"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "Request to cancel and verify"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        request_id = create_resp.json()["data"]["application"]["id"]

        api_client.delete(
            f"/users/me/team-requests/{request_id}",
            headers={"Authorization": f"Bearer {requester.token}"},
        )

        resp = api_client.get(
            "/users/me/team-requests",
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        assert resp.status_code == 200
        requests = resp.json()["data"]["requests"]
        request = next((r for r in requests if r["id"] == request_id), None)
        assert request is not None
        assert request["status"] == "CANCELED"

    def test_check_approved_request_status(
        self, setup_team_application: dict, api_client: httpx.Client
    ):
        owner = setup_team_application["owner"]
        requester = setup_team_application["requester"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "Request to approve and verify"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        request_id = create_resp.json()["data"]["application"]["id"]

        api_client.post(
            f"/teams/{team_id}/requests/{request_id}/approve",
            headers={"Authorization": f"Bearer {owner.token}"},
        )

        resp = api_client.get(
            "/users/me/team-requests",
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        assert resp.status_code == 200
        requests = resp.json()["data"]["requests"]
        request = next((r for r in requests if r["id"] == request_id), None)
        assert request is not None
        assert request["status"] == "APPROVED"

    def test_check_canceled_invitation_status(
        self, setup_team_application: dict, api_client: httpx.Client
    ):
        owner = setup_team_application["owner"]
        invitee = setup_team_application["invitee"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": invitee.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        invitation_id = create_resp.json()["data"]["invitation"]["id"]

        api_client.delete(
            f"/teams/{team_id}/invitations/{invitation_id}",
            headers={"Authorization": f"Bearer {owner.token}"},
        )

        resp = api_client.get(
            "/users/me/team-invitations",
            headers={"Authorization": f"Bearer {invitee.token}"},
        )
        assert resp.status_code == 200
        invitations = resp.json()["data"]["invitations"]
        invitation = next((i for i in invitations if i["id"] == invitation_id), None)
        assert invitation is not None
        assert invitation["status"] == "CANCELED"

    def test_check_declined_invitation_status(
        self, setup_team_application: dict, api_client: httpx.Client
    ):
        owner = setup_team_application["owner"]
        invitee = setup_team_application["invitee"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": invitee.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        invitation_id = create_resp.json()["data"]["invitation"]["id"]

        api_client.post(
            f"/users/me/team-invitations/{invitation_id}/decline",
            headers={"Authorization": f"Bearer {invitee.token}"},
        )

        resp = api_client.get(
            "/users/me/team-invitations",
            headers={"Authorization": f"Bearer {invitee.token}"},
        )
        assert resp.status_code == 200
        invitations = resp.json()["data"]["invitations"]
        invitation = next((i for i in invitations if i["id"] == invitation_id), None)
        assert invitation is not None
        assert invitation["status"] == "DECLINED"

    def test_check_accepted_invitation_status(
        self, setup_team_application: dict, api_client: httpx.Client
    ):
        owner = setup_team_application["owner"]
        invitee = setup_team_application["invitee"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": invitee.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        invitation_id = create_resp.json()["data"]["invitation"]["id"]

        api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {invitee.token}"},
        )

        resp = api_client.get(
            "/users/me/team-invitations",
            headers={"Authorization": f"Bearer {invitee.token}"},
        )
        assert resp.status_code == 200
        invitations = resp.json()["data"]["invitations"]
        invitation = next((i for i in invitations if i["id"] == invitation_id), None)
        assert invitation is not None
        assert invitation["status"] == "ACCEPTED"

    def test_request_fails_when_already_member(
        self, setup_team_application: dict, api_client: httpx.Client
    ):
        owner = setup_team_application["owner"]
        requester = setup_team_application["requester"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "Join to become member"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        request_id = create_resp.json()["data"]["application"]["id"]
        api_client.post(
            f"/teams/{team_id}/requests/{request_id}/approve",
            headers={"Authorization": f"Bearer {owner.token}"},
        )

        resp = api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "Try joining again"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        assert resp.status_code == 409, f"Expected 409 Conflict, got {resp.status_code}: {resp.text}"

    def test_request_fails_when_pending_exists(
        self, setup_team_application: dict, api_client: httpx.Client
    ):
        requester = setup_team_application["requester"]
        team_id = setup_team_application["team_id"]

        api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "First request"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )

        resp = api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "Second request"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        assert resp.status_code == 409, f"Expected 409 Conflict, got {resp.status_code}: {resp.text}"

    def test_invitation_fails_when_already_member(
        self, setup_team_application: dict, api_client: httpx.Client
    ):
        owner = setup_team_application["owner"]
        invitee = setup_team_application["invitee"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": invitee.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        invitation_id = create_resp.json()["data"]["invitation"]["id"]
        api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {invitee.token}"},
        )

        resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": invitee.user_id, "role": "ADMIN"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 409, f"Expected 409 Conflict, got {resp.status_code}: {resp.text}"

    def test_invitation_fails_when_pending_exists(
        self, setup_team_application: dict, api_client: httpx.Client
    ):
        owner = setup_team_application["owner"]
        invitee = setup_team_application["invitee"]
        team_id = setup_team_application["team_id"]

        api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": invitee.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )

        resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": invitee.user_id, "role": "ADMIN"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 409, f"Expected 409 Conflict, got {resp.status_code}: {resp.text}"

    def test_user_can_request_again_after_rejection(
        self, setup_team_application: dict, api_client: httpx.Client
    ):
        owner = setup_team_application["owner"]
        requester = setup_team_application["requester"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "First request"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        request_id = create_resp.json()["data"]["application"]["id"]

        api_client.post(
            f"/teams/{team_id}/requests/{request_id}/reject",
            headers={"Authorization": f"Bearer {owner.token}"},
        )

        resp = api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "Second request after rejection"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"

    def test_user_can_request_again_after_cancellation(
        self, setup_team_application: dict, api_client: httpx.Client
    ):
        requester = setup_team_application["requester"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "First request to cancel"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        request_id = create_resp.json()["data"]["application"]["id"]

        api_client.delete(
            f"/users/me/team-requests/{request_id}",
            headers={"Authorization": f"Bearer {requester.token}"},
        )

        resp = api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "Second request after cancellation"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"

    def test_owner_can_invite_again_after_decline(
        self, setup_team_application: dict, api_client: httpx.Client
    ):
        owner = setup_team_application["owner"]
        invitee = setup_team_application["invitee"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": invitee.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        invitation_id = create_resp.json()["data"]["invitation"]["id"]

        api_client.post(
            f"/users/me/team-invitations/{invitation_id}/decline",
            headers={"Authorization": f"Bearer {invitee.token}"},
        )

        resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": invitee.user_id, "role": "ADMIN"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"

    def test_invite_user_as_admin_and_verify_role(
        self, setup_team_application: dict, api_client: httpx.Client
    ):
        owner = setup_team_application["owner"]
        invitee = setup_team_application["invitee"]
        team_id = setup_team_application["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": invitee.user_id, "role": "ADMIN"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert create_resp.status_code == 201
        invitation_id = create_resp.json()["data"]["invitation"]["id"]

        accept_resp = api_client.post(
            f"/users/me/team-invitations/{invitation_id}/accept",
            headers={"Authorization": f"Bearer {invitee.token}"},
        )
        assert accept_resp.status_code == 204

        team_resp = api_client.get(
            f"/teams/{team_id}",
            headers={"Authorization": f"Bearer {invitee.token}"},
        )
        assert team_resp.status_code == 200
        team_data = team_resp.json()["data"]["team"]
        assert team_data.get("role") == "ADMIN"

    def test_third_request_after_rejection_and_cancellation(
        self, setup_team_application: dict, api_client: httpx.Client
    ):
        owner = setup_team_application["owner"]
        requester = setup_team_application["requester"]
        team_id = setup_team_application["team_id"]

        first_resp = api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "First request"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        assert first_resp.status_code == 201
        first_id = first_resp.json()["data"]["application"]["id"]

        api_client.post(
            f"/teams/{team_id}/requests/{first_id}/reject",
            headers={"Authorization": f"Bearer {owner.token}"},
        )

        second_resp = api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "Second request"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        assert second_resp.status_code == 201
        second_id = second_resp.json()["data"]["application"]["id"]

        api_client.delete(
            f"/users/me/team-requests/{second_id}",
            headers={"Authorization": f"Bearer {requester.token}"},
        )

        third_resp = api_client.post(
            f"/teams/{team_id}/requests",
            json={"message": "Third request after rejection and cancellation"},
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        assert third_resp.status_code == 201, f"Expected 201, got {third_resp.status_code}"
        third_id = third_resp.json()["data"]["application"]["id"]

        api_client.post(
            f"/teams/{team_id}/requests/{third_id}/approve",
            headers={"Authorization": f"Bearer {owner.token}"},
        )

        team_resp = api_client.get(
            f"/teams/{team_id}",
            headers={"Authorization": f"Bearer {requester.token}"},
        )
        assert team_resp.status_code == 200

    def test_verify_admin_role_in_invitation_list(
        self, setup_team_application: dict, api_client: httpx.Client
    ):
        owner = setup_team_application["owner"]
        invitee = setup_team_application["invitee"]
        team_id = setup_team_application["team_id"]

        api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": invitee.user_id, "role": "ADMIN"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )

        resp = api_client.get(
            "/users/me/team-invitations",
            params={"status": "PENDING"},
            headers={"Authorization": f"Bearer {invitee.token}"},
        )
        assert resp.status_code == 200
        invitations = resp.json()["data"]["invitations"]
        admin_invitation = next(
            (inv for inv in invitations if inv.get("team", {}).get("id") == team_id),
            None
        )
        assert admin_invitation is not None
        assert admin_invitation.get("role") == "ADMIN"
