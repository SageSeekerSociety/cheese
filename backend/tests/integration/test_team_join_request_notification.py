"""A join request in a team admin's feed can be answered from the feed.

Its card says which request it is and whether that request is still waiting,
so 待办 can offer approve and reject on it and stop offering them once it has
been answered.
"""

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, unique_int


def auth(user) -> dict:
    return {"Authorization": f"Bearer {user.token}"}


@pytest.fixture
def join_request(user_client: UserCreator, api_client: TestClient) -> dict:
    people = {}
    for role in ("owner", "requester"):
        user = user_client.create_user()
        user.token = user_client.login(api_client, user.username, user.password)
        people[role] = user
    team = api_client.post(
        "/teams",
        json={
            "name": f"Join Feed Team ({unique_int(10000000, 99999999)})",
            "intro": "",
            "description": "A team someone asks to join. " * 4,
            "avatarId": 1,
        },
        headers=auth(people["owner"]),
    )
    assert team.status_code == 201, team.text
    team_id = team.json()["data"]["team"]["id"]
    asked = api_client.post(f"/teams/{team_id}/join", headers=auth(people["requester"]))
    assert asked.status_code in (200, 201), asked.text
    return {**people, "team_id": team_id}


def request_card(api_client: TestClient, user) -> dict:
    resp = api_client.get(
        "/notifications", params={"type": "TEAM_JOIN_REQUEST"}, headers=auth(user)
    )
    assert resp.status_code == 200, resp.text
    (card,) = resp.json()["data"]["notifications"]
    return card


def members(api_client: TestClient, join_request: dict) -> set[int]:
    resp = api_client.get(
        f"/teams/{join_request['team_id']}/members",
        headers=auth(join_request["owner"]),
    )
    return {m["user"]["id"] for m in resp.json()["data"]["members"]}


def test_the_card_names_the_request_it_answers(api_client, join_request):
    card = request_card(api_client, join_request["owner"])

    application = card["entities"]["application"]
    assert application is not None
    assert application["status"] == "PENDING"
    assert card["entities"]["team"]["id"] == str(join_request["team_id"])


def test_approving_the_named_request_lets_the_requester_in(api_client, join_request):
    card = request_card(api_client, join_request["owner"])
    team_id = card["entities"]["team"]["id"]

    approved = api_client.post(
        f"/teams/{team_id}/requests/{card['entities']['application']['id']}/approve",
        headers=auth(join_request["owner"]),
    )

    assert approved.status_code in (200, 204), approved.text
    assert join_request["requester"].user_id in members(api_client, join_request)
    after = request_card(api_client, join_request["owner"])
    assert after["entities"]["application"]["status"] == "APPROVED"


def test_a_rejected_request_no_longer_reads_as_waiting(api_client, join_request):
    card = request_card(api_client, join_request["owner"])
    team_id = card["entities"]["team"]["id"]

    rejected = api_client.post(
        f"/teams/{team_id}/requests/{card['entities']['application']['id']}/reject",
        headers=auth(join_request["owner"]),
    )

    assert rejected.status_code in (200, 204), rejected.text
    assert join_request["requester"].user_id not in members(api_client, join_request)
    after = request_card(api_client, join_request["owner"])
    assert after["entities"]["application"]["status"] == "REJECTED"
