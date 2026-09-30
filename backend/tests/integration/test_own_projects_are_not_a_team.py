"""A person's own projects sit under their own name, not under a team.

Underneath, a person's projects belong to a team with only them in it. That
team does not show: it goes by the person's nickname and avatar, and it holds
no name another team could want.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _own(api_client: TestClient, token: str) -> dict:
    resp = api_client.get("/teams/my-teams", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    [own] = [t for t in resp.json()["data"]["teams"] if t["personal"]]
    return own


def test_own_projects_go_under_the_persons_nickname_and_avatar(
    api_client: TestClient, user_client: UserCreator
):
    user = user_client.create_user(nickname="林夏", avatar_id=7)
    token = user_client.login(api_client, user.username, user.password)

    own = _own(api_client, token)

    assert (own["name"], own["avatarId"]) == ("林夏", 7)
    resp = api_client.get(f"/teams/{own['id']}", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["team"]["name"] == "林夏"


def test_a_team_may_be_called_what_everyones_own_projects_used_to_be(
    api_client: TestClient, user_client: UserCreator
):
    user = user_client.create_user()
    token = user_client.login(api_client, user.username, user.password)
    _own(api_client, token)

    resp = api_client.post(
        "/teams",
        json={
            "name": "个人",
            "intro": "一支叫这个名字的队",
            "description": "",
            "avatarId": 1,
        },
        headers=_auth(token),
    )

    assert resp.status_code == 201, resp.text
