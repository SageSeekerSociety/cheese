"""A team invitation waits in the invitee's 等你处理 until it is answered.

The row says which invitation it is, who sent it and into which team, so 待办
can offer accept and decline on it; once answered it is no longer there. The
notification that announced it is not in the feed as well: that would list the
same thing twice, and leave a line saying "waiting for you" after it was done.
"""

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, unique_int


def auth(user) -> dict:
    return {"Authorization": f"Bearer {user.token}"}


@pytest.fixture
def invitation(user_client: UserCreator, api_client: TestClient) -> dict:
    people = {}
    for role in ("owner", "invitee"):
        user = user_client.create_user()
        user.token = user_client.login(api_client, user.username, user.password)
        people[role] = user
    name = f"Invite Feed Team ({unique_int(10000000, 99999999)})"
    team = api_client.post(
        "/teams",
        json={
            "name": name,
            "intro": "",
            "description": "A team to invite someone into. " * 4,
            "avatarId": 1,
        },
        headers=auth(people["owner"]),
    )
    assert team.status_code == 201, team.text
    team_id = team.json()["data"]["team"]["id"]
    sent = api_client.post(
        f"/teams/{team_id}/invitations",
        json={"userId": people["invitee"].user_id, "role": "MEMBER"},
        headers=auth(people["owner"]),
    )
    assert sent.status_code == 201, sent.text
    return {
        **people,
        "team_id": team_id,
        "team_name": name,
        "invitation_id": sent.json()["data"]["invitation"]["id"],
    }


def waiting(api_client: TestClient, user) -> list[dict]:
    resp = api_client.get("/awaiting-me", headers=auth(user))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["data"]


def feed(api_client: TestClient, user) -> list[dict]:
    resp = api_client.get("/notifications", headers=auth(user))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["notifications"]


def test_a_pending_invitation_waits_on_the_invitee(api_client, invitation):
    [row] = waiting(api_client, invitation["invitee"])

    assert row["kind"] == "team_invite"
    assert row["id"] == str(invitation["invitation_id"])
    assert row["placeId"] == str(invitation["team_id"])
    assert row["place"]["name"] == invitation["team_name"]
    assert row["person"]["handle"] == invitation["owner"].username
    assert row["role"] == "MEMBER"
    assert [n["type"] for n in feed(api_client, invitation["invitee"])] == []


def test_accepting_it_joins_the_team_and_it_stops_waiting(api_client, invitation):
    [row] = waiting(api_client, invitation["invitee"])

    accepted = api_client.post(
        f"/users/me/team-invitations/{row['id']}/accept",
        headers=auth(invitation["invitee"]),
    )

    assert accepted.status_code in (200, 204), accepted.text
    members = api_client.get(
        f"/teams/{invitation['team_id']}/members", headers=auth(invitation["invitee"])
    )
    assert invitation["invitee"].user_id in {
        m["user"]["id"] for m in members.json()["data"]["members"]
    }
    assert waiting(api_client, invitation["invitee"]) == []


def test_a_declined_invitation_stops_waiting(api_client, invitation):
    declined = api_client.post(
        f"/users/me/team-invitations/{invitation['invitation_id']}/decline",
        headers=auth(invitation["invitee"]),
    )
    assert declined.status_code in (200, 204), declined.text

    assert waiting(api_client, invitation["invitee"]) == []


def test_the_inviter_is_not_waited_on(api_client, invitation):
    assert waiting(api_client, invitation["owner"]) == []
