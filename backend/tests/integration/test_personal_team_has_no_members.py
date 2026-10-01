"""A personal team is one person's: nobody but its owner gets in.

Its owner can neither add anyone directly nor invite anyone, and an invitation
sent before that was refused cannot be accepted. A shared team takes members
the same ways as before.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.domain.team.models import (
    ApplicationStatus,
    ApplicationType,
    TeamMembershipApplication,
)
from tests.integration.conftest import UserCreator, unique_int


def auth(user) -> dict:
    return {"Authorization": f"Bearer {user.token}"}


@pytest.fixture
def people(user_client: UserCreator, api_client: TestClient) -> dict:
    out = {}
    for role in ("owner", "friend"):
        user = user_client.create_user()
        user.token = user_client.login(api_client, user.username, user.password)
        out[role] = user
    return out


def my_teams(api_client: TestClient, user) -> list[dict]:
    resp = api_client.get("/teams/my-teams", headers=auth(user))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["teams"]


def personal_team_of(api_client: TestClient, user) -> dict:
    return next(t for t in my_teams(api_client, user) if t["personal"])


def member_ids(api_client: TestClient, team_id: int, user) -> set[int]:
    resp = api_client.get(f"/teams/{team_id}/members", headers=auth(user))
    assert resp.status_code == 200, resp.text
    return {m["user"]["id"] for m in resp.json()["data"]["members"]}


def pending_invitations(api_client: TestClient, user) -> list[dict]:
    resp = api_client.get(
        "/users/me/team-invitations",
        params={"status": "PENDING"},
        headers=auth(user),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["invitations"]


def test_the_owner_cannot_add_anyone_to_a_personal_team(api_client, people):
    team = personal_team_of(api_client, people["owner"])
    resp = api_client.post(
        f"/teams/{team['id']}/members",
        json={"userId": people["friend"].user_id, "role": "MEMBER"},
        headers=auth(people["owner"]),
    )
    assert resp.status_code == 400, resp.text
    assert "personal team" in resp.json()["error"]["message"]
    assert member_ids(api_client, team["id"], people["owner"]) == {
        people["owner"].user_id
    }
    assert team["id"] not in {t["id"] for t in my_teams(api_client, people["friend"])}


def test_the_owner_cannot_invite_anyone_to_a_personal_team(api_client, people):
    team = personal_team_of(api_client, people["owner"])
    resp = api_client.post(
        f"/teams/{team['id']}/invitations",
        json={"userId": people["friend"].user_id, "role": "MEMBER"},
        headers=auth(people["owner"]),
    )
    assert resp.status_code == 400, resp.text
    assert pending_invitations(api_client, people["friend"]) == []


def test_an_invitation_sent_earlier_cannot_be_accepted(
    api_client, people, db_session, _portal
):
    team = personal_team_of(api_client, people["owner"])

    async def invite_the_old_way() -> int:
        now = datetime.now(UTC)
        app = TeamMembershipApplication(
            user_id=people["friend"].user_id,
            team_id=team["id"],
            initiator_id=people["owner"].user_id,
            type=ApplicationType.INVITATION.value,
            status=ApplicationStatus.PENDING.value,
            role="MEMBER",
            message="",
            created_at=now,
            updated_at=now,
        )
        db_session.add(app)
        await db_session.flush()
        return app.id

    invitation_id = _portal.call(invite_the_old_way)
    resp = api_client.post(
        f"/users/me/team-invitations/{invitation_id}/accept",
        headers=auth(people["friend"]),
    )
    assert resp.status_code == 400, resp.text
    assert member_ids(api_client, team["id"], people["owner"]) == {
        people["owner"].user_id
    }


def test_a_shared_team_still_takes_members(api_client, people, user_client):
    resp = api_client.post(
        "/teams",
        json={
            "name": f"Shared Team {unique_int()}",
            "intro": "",
            "description": "",
            "avatarId": 1,
        },
        headers=auth(people["owner"]),
    )
    assert resp.status_code == 201, resp.text
    team_id = resp.json()["data"]["team"]["id"]

    resp = api_client.post(
        f"/teams/{team_id}/members",
        json={"userId": people["friend"].user_id, "role": "MEMBER"},
        headers=auth(people["owner"]),
    )
    assert resp.status_code == 201, resp.text

    invitee = user_client.create_user()
    invitee.token = user_client.login(api_client, invitee.username, invitee.password)
    resp = api_client.post(
        f"/teams/{team_id}/invitations",
        json={"userId": invitee.user_id, "role": "MEMBER"},
        headers=auth(people["owner"]),
    )
    assert resp.status_code == 201, resp.text
    invitation_id = resp.json()["data"]["invitation"]["id"]
    resp = api_client.post(
        f"/users/me/team-invitations/{invitation_id}/accept",
        headers=auth(invitee),
    )
    assert resp.status_code == 204, resp.text

    assert member_ids(api_client, team_id, people["owner"]) == {
        people["owner"].user_id,
        people["friend"].user_id,
        invitee.user_id,
    }
