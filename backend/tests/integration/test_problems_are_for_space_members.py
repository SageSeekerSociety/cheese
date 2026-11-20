"""A 题目 is seen and claimed only by the members of the space it is published in.

Product decision (2026-10-26): someone signed in but not in the space cannot
open the problem, cannot find it in the space's list or by searching it, and
cannot claim it — even when the problem has no access control (email-domain
allow list) of its own. A member of the space can do all three.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    unique_int,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _board_with_problem(api_client: TestClient, user_client: UserCreator) -> dict:
    """A space, its owner, and one approved problem without access control."""
    owner = user_client.create_user()
    owner_token = user_client.login(api_client, owner.username, owner.password)
    word = f"lighthouse{unique_int(10000000, 99999999)}"
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Members only ({word})",
            "intro": "一门课",
            "description": "一个题目板",
            "avatarId": 1,
            "taskTemplates": [],
        },
        headers=_auth(owner_token),
    )
    assert resp.status_code == 201, resp.text
    space_id = resp.json()["data"]["space"]["id"]

    resp = api_client.post(
        "/tasks",
        json={
            "name": f"{word} 实验",
            "intro": "题",
            "description": '{"type":"doc","content":[]}',
            "space": space_id,
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": int(time.time() * 1000) + 7 * 86400 * 1000,
        },
        headers=_auth(owner_token),
    )
    assert resp.status_code == 200, resp.text
    task = resp.json()["data"]["task"]
    assert task["accessControlEnabled"] is False
    resp = api_client.patch(
        f"/tasks/{task['id']}",
        json={"approved": "APPROVED"},
        headers=_auth(owner_token),
    )
    assert resp.status_code == 200, resp.text
    return {
        "owner_token": owner_token,
        "space_id": space_id,
        "task_id": task["id"],
        "word": word,
    }


def _person(
    api_client: TestClient, user_client: UserCreator, board: dict, *, member: bool
) -> tuple[int, str]:
    user = user_client.create_user()
    token = user_client.login(api_client, user.username, user.password)
    if member:
        resp = api_client.post(
            f"/spaces/{board['space_id']}/members",
            json={"userId": user.user_id},
            headers=_auth(board["owner_token"]),
        )
        assert resp.status_code == 201, resp.text
    return user.user_id, token


def _listed_ids(api_client: TestClient, board: dict, token: str, **params) -> set:
    resp = api_client.get(
        "/tasks",
        params={"space": board["space_id"], **params},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return {t["id"] for t in resp.json()["data"]["tasks"]}


def _participants(api_client: TestClient, board: dict) -> list:
    resp = api_client.get(
        f"/tasks/{board['task_id']}/participants",
        headers=_auth(board["owner_token"]),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["participants"]


def test_someone_outside_the_space_cannot_open_the_problem(
    api_client: TestClient, user_client: UserCreator
):
    board = _board_with_problem(api_client, user_client)
    _, outsider = _person(api_client, user_client, board, member=False)
    _, member = _person(api_client, user_client, board, member=True)

    url = f"/tasks/{board['task_id']}"
    assert api_client.get(url, headers=_auth(outsider)).status_code == 404
    assert api_client.get(url, headers=_auth(member)).status_code == 200


def test_someone_outside_the_space_cannot_find_the_problem(
    api_client: TestClient, user_client: UserCreator
):
    board = _board_with_problem(api_client, user_client)
    _, outsider = _person(api_client, user_client, board, member=False)
    _, member = _person(api_client, user_client, board, member=True)

    assert _listed_ids(api_client, board, outsider) == set()
    assert _listed_ids(api_client, board, outsider, keywords=board["word"]) == set()
    assert _listed_ids(api_client, board, member) == {board["task_id"]}
    assert _listed_ids(api_client, board, member, keywords=board["word"]) == {
        board["task_id"]
    }


@pytest.mark.parametrize("route", ["participations/user", "participants"])
def test_someone_outside_the_space_cannot_claim_the_problem(
    api_client: TestClient, user_client: UserCreator, route: str
):
    board = _board_with_problem(api_client, user_client)
    _, outsider = _person(api_client, user_client, board, member=False)
    member_id, member = _person(api_client, user_client, board, member=True)
    claim = f"/tasks/{board['task_id']}/{route}"
    body = {"agreeMentors": True} if route == "participations/user" else {}

    refused = api_client.post(claim, json=body, headers=_auth(outsider))
    assert refused.status_code == 404, refused.text
    assert _participants(api_client, board) == []

    claimed = api_client.post(claim, json=body, headers=_auth(member))
    assert claimed.status_code in (200, 201), claimed.text
    assert [p["member"]["id"] for p in _participants(api_client, board)] == [member_id]
