"""领取一道题的规矩。

- 出题人的领取名单里能看到每人的截止时间和申请理由。
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


@pytest.fixture
def board(user_client: UserCreator, api_client: TestClient) -> dict:
    creator = user_client.create_user()
    token = user_client.login(api_client, creator.username, creator.password)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"One claim ({unique_int(10000000, 99999999)})",
            "intro": "一块板",
            "description": "一人一题",
            "avatarId": 1,
            "taskTemplates": [],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]
    return {
        "token": token,
        "space_id": space["id"],
        "category_id": space.get("defaultCategoryId"),
    }


def _member(user_client: UserCreator, api_client: TestClient, board: dict):
    user = user_client.create_user()
    token = user_client.login(api_client, user.username, user.password)
    resp = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": user.user_id},
        headers=_auth(board["token"]),
    )
    assert resp.status_code == 201, resp.text
    return user, token


def _task(api_client: TestClient, board: dict, submitter_type: str) -> int:
    resp = api_client.post(
        "/tasks",
        json={
            "name": f"{submitter_type} 题",
            "intro": "题",
            "description": '{"type":"doc","content":[]}',
            "space": board["space_id"],
            "categoryId": board["category_id"],
            "submitterType": submitter_type,
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": int(time.time() * 1000) + 7 * 86400 * 1000,
        },
        headers=_auth(board["token"]),
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(board["token"]),
    )
    assert resp.status_code == 200, resp.text
    return task_id


def _team(api_client: TestClient, token: str) -> int:
    resp = api_client.post(
        "/teams",
        json={
            "name": f"队 {unique_int(10000000, 99999999)}",
            "intro": "一支队伍",
            "description": "为了这道题组的队",
            "avatarId": 1,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["team"]["id"]


def _participants(api_client: TestClient, task_id: int, token: str) -> list[dict]:
    resp = api_client.get(f"/tasks/{task_id}/participants", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["participants"]
def test_the_roster_shows_each_claims_deadline_and_reason(
    api_client: TestClient, user_client: UserCreator, board: dict
):
    _, token = _member(user_client, api_client, board)
    task_id = _task(api_client, board, "USER")
    resp = api_client.post(
        f"/tasks/{task_id}/participations/user",
        json={"applyReason": "想练一下对比实验"},
        headers=_auth(token),
    )
    assert resp.status_code in (200, 201), resp.text

    claim = _participants(api_client, task_id, board["token"])[0]
    assert claim["applyReason"] == "想练一下对比实验"

    deadline = int(time.time() * 1000) + 3 * 86400 * 1000
    resp = api_client.patch(
        f"/tasks/{task_id}/participants/{claim['id']}",
        json={"approved": "APPROVED", "deadline": deadline},
        headers=_auth(board["token"]),
    )
    assert resp.status_code == 200, resp.text
    claim = _participants(api_client, task_id, board["token"])[0]
    assert abs(claim["deadline"] - deadline) < 1000
