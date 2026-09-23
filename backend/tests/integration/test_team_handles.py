"""A team's handle: the name it goes by in links, in the users' namespace.

A shared team has its own handle — chosen, or ``team-<id>`` until its owner
picks one. A personal team goes by its owner's username and opens only for them.
"""

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, unique_int


def auth(user) -> dict:
    return {"Authorization": f"Bearer {user.token}"}


@pytest.fixture
def people(user_client: UserCreator, api_client: TestClient) -> dict:
    out = {}
    for role in ("owner", "outsider"):
        user = user_client.create_user()
        user.token = user_client.login(api_client, user.username, user.password)
        out[role] = user
    return out


def create_team(api_client, owner, **extra):
    resp = api_client.post(
        "/teams",
        json={"name": f"Handle Team {unique_int()}", **extra},
        headers=auth(owner),
    )
    return resp


def by_handle(api_client, handle, user):
    return api_client.get(f"/teams/by-handle/{handle}", headers=auth(user))


def test_a_chosen_handle_opens_the_team(api_client, people):
    handle = f"crew{unique_int()}"
    resp = create_team(api_client, people["owner"], handle=handle)
    assert resp.status_code == 201, resp.text
    team = resp.json()["data"]["team"]
    assert team["handle"] == handle
    found = by_handle(api_client, handle.upper(), people["outsider"])
    assert found.status_code == 200, found.text
    assert found.json()["data"]["team"]["id"] == team["id"]
    assert found.json()["data"]["team"]["joinStatus"] == "none"


def test_without_a_handle_a_team_is_named_by_its_id(api_client, people):
    team = create_team(api_client, people["owner"]).json()["data"]["team"]
    assert team["handle"] == f"team-{team['id']}"
    assert by_handle(api_client, team["handle"], people["owner"]).status_code == 200


def test_a_team_can_be_renamed(api_client, people):
    team = create_team(api_client, people["owner"]).json()["data"]["team"]
    handle = f"renamed{unique_int()}"
    resp = api_client.patch(
        f"/teams/{team['id']}", json={"handle": handle}, headers=auth(people["owner"])
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["team"]["handle"] == handle
    assert by_handle(api_client, handle, people["owner"]).status_code == 200
    assert by_handle(api_client, team["handle"], people["owner"]).status_code == 404


@pytest.mark.parametrize(
    "bad", ["ab", "has space", "system", "cheese-x1", "team-42", "explore", "Mine"]
)
def test_a_handle_outside_the_rule_is_refused(api_client, people, bad):
    assert create_team(api_client, people["owner"], handle=bad).status_code == 400


def test_a_handle_held_by_a_user_or_a_team_is_taken(api_client, people):
    held_by_user = people["outsider"].username
    assert (
        create_team(
            api_client, people["owner"], handle=held_by_user.upper()
        ).status_code
        == 409
    )
    handle = f"first{unique_int()}"
    assert create_team(api_client, people["owner"], handle=handle).status_code == 201
    assert create_team(api_client, people["owner"], handle=handle).status_code == 409


def test_search_finds_a_team_by_handle(api_client, people):
    handle = f"findme{unique_int()}"
    team = create_team(api_client, people["owner"], handle=handle).json()["data"][
        "team"
    ]
    resp = api_client.get(
        "/teams", params={"query": handle}, headers=auth(people["outsider"])
    )
    assert team["id"] in [t["id"] for t in resp.json()["data"]["teams"]]


def test_a_stealth_team_is_not_found_by_handle_from_outside(api_client, people):
    handle = f"hidden{unique_int()}"
    team = create_team(api_client, people["owner"], handle=handle).json()["data"][
        "team"
    ]
    api_client.patch(
        f"/teams/{team['id']}",
        json={"visibility": "stealth"},
        headers=auth(people["owner"]),
    )
    assert by_handle(api_client, handle, people["outsider"]).status_code == 404
    assert by_handle(api_client, "nobody-here-9", people["outsider"]).status_code == 404
    assert by_handle(api_client, handle, people["owner"]).status_code == 200


def test_your_username_opens_your_personal_team_and_only_for_you(api_client, people):
    owner = people["owner"]
    mine = api_client.get("/teams/my-teams", headers=auth(owner)).json()["data"]
    personal = next(t for t in mine["teams"] if t["personal"])
    assert personal["handle"] == owner.username
    resp = by_handle(api_client, owner.username, owner)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["team"]["id"] == personal["id"]
    assert by_handle(api_client, owner.username, people["outsider"]).status_code == 404
    renamed = api_client.patch(
        f"/teams/{personal['id']}", json={"handle": "mine-now"}, headers=auth(owner)
    )
    assert renamed.status_code == 400
