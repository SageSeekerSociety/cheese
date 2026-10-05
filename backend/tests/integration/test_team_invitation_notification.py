"""A team invitation in the invitee's feed can be answered from the feed.

Its card says which invitation it is and whether that invitation is still
waiting, so 待办 can offer accept and decline on it and stop offering them once
it has been answered.
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
    team = api_client.post(
        "/teams",
        json={
            "name": f"Invite Feed Team ({unique_int(10000000, 99999999)})",
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
        "invitation_id": sent.json()["data"]["invitation"]["id"],
    }


def invitation_card(api_client: TestClient, user) -> dict:
    resp = api_client.get(
        "/notifications", params={"type": "TEAM_INVITATION"}, headers=auth(user)
    )
    assert resp.status_code == 200, resp.text
    (card,) = resp.json()["data"]["notifications"]
    return card


def test_the_card_names_the_invitation_it_answers(api_client, invitation):
    card = invitation_card(api_client, invitation["invitee"])

    application = card["entities"]["application"]
    assert application is not None
    assert application["id"] == str(invitation["invitation_id"])
    assert application["status"] == "PENDING"


def test_answering_the_named_invitation_joins_the_team(api_client, invitation):
    card = invitation_card(api_client, invitation["invitee"])

    accepted = api_client.post(
        f"/users/me/team-invitations/{card['entities']['application']['id']}/accept",
        headers=auth(invitation["invitee"]),
    )

    assert accepted.status_code in (200, 204), accepted.text
    members = api_client.get(
        f"/teams/{invitation['team_id']}/members", headers=auth(invitation["invitee"])
    )
    assert invitation["invitee"].user_id in {
        m["user"]["id"] for m in members.json()["data"]["members"]
    }


def test_an_answered_invitation_no_longer_reads_as_waiting(api_client, invitation):
    declined = api_client.post(
        f"/users/me/team-invitations/{invitation['invitation_id']}/decline",
        headers=auth(invitation["invitee"]),
    )
    assert declined.status_code in (200, 204), declined.text

    card = invitation_card(api_client, invitation["invitee"])

    assert card["entities"]["application"]["status"] == "DECLINED"
