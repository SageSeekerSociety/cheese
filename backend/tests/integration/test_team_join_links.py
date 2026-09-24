"""Who can find a team, and how its join link lets people in.

A public team is found by search and opens by id for anyone; a stealth team
does neither and is reached only through its link. Joining — from the profile
or the link — lands on the roster or waits for an owner or admin, as the team's
approval says.
"""

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, unique_int


def auth(user) -> dict:
    return {"Authorization": f"Bearer {user.token}"}


@pytest.fixture
def people(user_client: UserCreator, api_client: TestClient) -> dict:
    out = {}
    for role in ("owner", "admin", "member", "outsider"):
        user = user_client.create_user()
        user.token = user_client.login(api_client, user.username, user.password)
        out[role] = user
    return out


@pytest.fixture
def team(api_client: TestClient, people: dict) -> dict:
    name = f"Join Link Team {unique_int()}"
    resp = api_client.post(
        "/teams",
        json={"name": name, "intro": "intro", "description": "desc", "avatarId": 1},
        headers=auth(people["owner"]),
    )
    assert resp.status_code == 201, resp.text
    team_id = resp.json()["data"]["team"]["id"]
    for role in ("admin", "member"):
        resp = api_client.post(
            f"/teams/{team_id}/members",
            json={"userId": people[role].user_id, "role": role.upper()},
            headers=auth(people["owner"]),
        )
        assert resp.status_code in (200, 201), resp.text
    return {"id": team_id, "name": name}


def set_visibility(api_client, team, owner, visibility):
    resp = api_client.patch(
        f"/teams/{team['id']}", json={"visibility": visibility}, headers=auth(owner)
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()["data"]["team"]
    assert updated["visibility"] == visibility
    # The page that sent it keeps showing the workspace, not the outsider view.
    assert updated["joinStatus"] == "member"


def join_link(api_client, team, user, **settings):
    if settings:
        resp = api_client.patch(
            f"/teams/{team['id']}/join-link", json=settings, headers=auth(user)
        )
    else:
        resp = api_client.get(f"/teams/{team['id']}/join-link", headers=auth(user))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def search(api_client, user, query) -> list[int]:
    resp = api_client.get("/teams", params={"query": query}, headers=auth(user))
    assert resp.status_code == 200, resp.text
    return [t["id"] for t in resp.json()["data"]["teams"]]


def is_member(api_client, team, user) -> bool:
    resp = api_client.get(f"/teams/{team['id']}", headers=auth(user))
    return resp.status_code == 200 and resp.json()["data"]["team"]["joinStatus"] == (
        "member"
    )


def test_a_new_team_is_public_and_asks_for_approval(api_client, people, team):
    resp = api_client.get(f"/teams/{team['id']}", headers=auth(people["outsider"]))
    assert resp.status_code == 200, resp.text
    profile = resp.json()["data"]["team"]
    assert profile["visibility"] == "public"
    assert profile["joinApproval"] is True
    assert profile["joinStatus"] == "none"
    assert team["id"] in search(api_client, people["outsider"], team["name"])


def test_search_needs_a_signed_in_caller(api_client, team):
    assert api_client.get("/teams", params={"query": team["name"]}).status_code == 401


def test_a_stealth_team_is_hidden_from_everyone_outside_it(api_client, people, team):
    set_visibility(api_client, team, people["owner"], "stealth")
    outsider = people["outsider"]
    assert team["id"] not in search(api_client, outsider, team["name"])
    assert team["id"] not in search(api_client, outsider, str(team["id"]))
    missing = api_client.get("/teams/999999999", headers=auth(outsider))
    hidden = api_client.get(f"/teams/{team['id']}", headers=auth(outsider))
    assert hidden.status_code == missing.status_code == 404
    assert (
        api_client.post(f"/teams/{team['id']}/join", headers=auth(outsider)).status_code
        == 404
    )
    # Its own people still open it by id.
    assert is_member(api_client, team, people["member"])


def test_the_join_link_reaches_a_stealth_team(api_client, people, team):
    set_visibility(api_client, team, people["owner"], "stealth")
    token = join_link(api_client, team, people["admin"])["token"]
    outsider = people["outsider"]
    resp = api_client.get(f"/team-invites/{token}", headers=auth(outsider))
    assert resp.status_code == 200, resp.text
    profile = resp.json()["data"]["team"]
    assert (profile["id"], profile["name"]) == (team["id"], team["name"])
    assert profile["joinStatus"] == "none"
    resp = api_client.post(
        f"/team-invites/{token}/join",
        json={"message": "from the wiki"},
        headers=auth(outsider),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["team"]["joinStatus"] == "pending"
    assert not is_member(api_client, team, outsider)

    requests = api_client.get(
        f"/teams/{team['id']}/requests",
        params={"status": "PENDING"},
        headers=auth(people["admin"]),
    ).json()["data"]["applications"]
    [request] = [r for r in requests if r["user"]["id"] == outsider.user_id]
    assert request["message"] == "from the wiki"
    approve = api_client.post(
        f"/teams/{team['id']}/requests/{request['id']}/approve",
        headers=auth(people["admin"]),
    )
    assert approve.status_code == 204, approve.text
    assert is_member(api_client, team, outsider)
    assert team["id"] in [
        t["id"]
        for t in api_client.get("/teams/my-teams", headers=auth(outsider)).json()[
            "data"
        ]["teams"]
    ]


def test_without_approval_the_link_lets_people_straight_in(api_client, people, team):
    link = join_link(api_client, team, people["owner"], approval=False)
    assert link["approval"] is False
    resp = api_client.post(
        f"/team-invites/{link['token']}/join", headers=auth(people["outsider"])
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["team"]["joinStatus"] == "member"
    assert is_member(api_client, team, people["outsider"])


def test_joining_from_a_public_profile_follows_the_same_switch(
    api_client, people, team
):
    join_link(api_client, team, people["owner"], approval=False)
    resp = api_client.post(
        f"/teams/{team['id']}/join", headers=auth(people["outsider"])
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["team"]["joinStatus"] == "member"


def test_the_link_is_permanent_until_reset(api_client, people, team):
    first = join_link(api_client, team, people["owner"])
    assert join_link(api_client, team, people["admin"]) == first
    reset = api_client.post(
        f"/teams/{team['id']}/join-link/reset", headers=auth(people["admin"])
    )
    assert reset.status_code == 200, reset.text
    renewed = reset.json()["data"]
    assert renewed["token"] != first["token"]
    assert renewed["approval"] == first["approval"]
    outsider = auth(people["outsider"])
    old = f"/team-invites/{first['token']}"
    assert api_client.get(old, headers=outsider).status_code == 404
    assert api_client.post(old + "/join", headers=outsider).status_code == 404
    assert (
        api_client.get(
            f"/team-invites/{renewed['token']}", headers=outsider
        ).status_code
        == 200
    )
    # Resetting takes nobody out.
    assert is_member(api_client, team, people["member"])


def test_only_owners_and_admins_handle_the_link(api_client, people, team):
    base = f"/teams/{team['id']}/join-link"
    for role in ("member", "outsider"):
        headers = auth(people[role])
        assert api_client.get(base, headers=headers).status_code == 403
        assert (
            api_client.patch(
                base, json={"approval": False}, headers=headers
            ).status_code
            == 403
        )
        assert api_client.post(base + "/reset", headers=headers).status_code == 403
    for role in ("member", "outsider"):
        assert (
            api_client.patch(
                f"/teams/{team['id']}",
                json={"visibility": "stealth"},
                headers=auth(people[role]),
            ).status_code
            == 403
        )


def test_a_personal_team_is_nobody_elses(api_client, people):
    mine = api_client.get("/teams/my-teams", headers=auth(people["owner"])).json()[
        "data"
    ]["teams"]
    personal = next(t for t in mine if t["personal"])
    outsider = auth(people["outsider"])
    assert (
        api_client.get(f"/teams/{personal['id']}", headers=outsider).status_code == 404
    )
    assert (
        api_client.post(f"/teams/{personal['id']}/join", headers=outsider).status_code
        == 404
    )
    assert personal["id"] not in search(api_client, people["outsider"], "个人")
    assert (
        api_client.get(
            f"/teams/{personal['id']}/join-link", headers=auth(people["owner"])
        ).status_code
        == 400
    )


def test_a_link_to_nothing_is_not_found(api_client, people):
    resp = api_client.get("/team-invites/not-a-token", headers=auth(people["owner"]))
    assert resp.status_code == 404
