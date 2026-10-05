"""A team admin invites someone by the username or email they know.

Nobody knows another person's numeric id, so the invite dialog finds the person
by exact username or email first and invites whoever that turned out to be.
"""

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, unique_int


def auth(user) -> dict:
    return {"Authorization": f"Bearer {user.token}"}


@pytest.fixture
def team(user_client: UserCreator, api_client: TestClient) -> dict:
    people = {}
    for role in ("owner", "friend"):
        user = user_client.create_user()
        user.token = user_client.login(api_client, user.username, user.password)
        people[role] = user
    created = api_client.post(
        "/teams",
        json={
            "name": f"Invite By Name ({unique_int(10000000, 99999999)})",
            "intro": "",
            "description": "A team someone is invited into by name. " * 3,
            "avatarId": 1,
        },
        headers=auth(people["owner"]),
    )
    assert created.status_code == 201, created.text
    return {**people, "id": created.json()["data"]["team"]["id"]}


def invite_whoever_is_found(api_client: TestClient, team: dict, q: str):
    found = api_client.get(
        "/users/lookup", params={"q": q}, headers=auth(team["owner"])
    )
    assert found.status_code == 200, found.text
    return api_client.post(
        f"/teams/{team['id']}/invitations",
        json={"userId": found.json()["data"]["id"], "role": "MEMBER"},
        headers=auth(team["owner"]),
    )


def pending_team_ids(api_client: TestClient, user) -> set[int]:
    resp = api_client.get(
        "/users/me/team-invitations", params={"status": "PENDING"}, headers=auth(user)
    )
    assert resp.status_code == 200, resp.text
    return {inv["team"]["id"] for inv in resp.json()["data"]["invitations"]}


def test_an_invitation_by_username_reaches_that_person(api_client, team):
    sent = invite_whoever_is_found(api_client, team, team["friend"].username)

    assert sent.status_code == 201, sent.text
    assert team["id"] in pending_team_ids(api_client, team["friend"])


def test_an_invitation_by_email_reaches_that_person(api_client, team):
    sent = invite_whoever_is_found(api_client, team, team["friend"].email)

    assert sent.status_code == 201, sent.text
    assert team["id"] in pending_team_ids(api_client, team["friend"])
