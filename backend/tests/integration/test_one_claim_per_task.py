"""领取一道题的规矩。

- 团队题：队里只要有人已经通过别的团队领了这道题（等批也算），这个团队就领不了；
  那份领取被拒绝之后可以。
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


def _claim_as_team(api_client: TestClient, task_id: int, team_id: int, token: str):
    return api_client.post(
        f"/tasks/{task_id}/participations/team",
        json={"teamId": team_id},
        headers=_auth(token),
    )
def _participants(api_client: TestClient, task_id: int, token: str) -> list[dict]:
    resp = api_client.get(f"/tasks/{task_id}/participants", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["participants"]
def _team_eligibility(api_client: TestClient, task_id: int, token: str) -> dict:
    resp = api_client.get(
        f"/tasks/{task_id}", params={"queryJoinability": True}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    teams = resp.json()["data"]["task"]["participationEligibility"]["teams"]
    return {t["team"]["id"]: t["eligibility"] for t in teams}
def test_a_person_cannot_claim_a_task_again_through_another_team(
    api_client: TestClient, user_client: UserCreator, board: dict
):
    _, token = _member(user_client, api_client, board)
    first, second = _team(api_client, token), _team(api_client, token)
    task_id = _task(api_client, board, "TEAM")

    assert _claim_as_team(api_client, task_id, first, token).status_code in (200, 201)

    # 两支队里都有这个人：第二支队伍在资格里就是不可领，硬领也被拒。
    eligibility = _team_eligibility(api_client, task_id, token)
    assert eligibility[second]["eligible"] is False
    assert "MEMBER_ALREADY_PARTICIPATING" in {
        r["code"] for r in eligibility[second]["reasons"]
    }
    assert _claim_as_team(api_client, task_id, second, token).status_code == 400
    assert [
        p["memberId"] for p in _participants(api_client, task_id, board["token"])
    ] == [first]
def test_a_rejected_claim_frees_its_members_to_claim_with_another_team(
    api_client: TestClient, user_client: UserCreator, board: dict
):
    _, token = _member(user_client, api_client, board)
    first, second = _team(api_client, token), _team(api_client, token)
    task_id = _task(api_client, board, "TEAM")
    assert _claim_as_team(api_client, task_id, first, token).status_code in (200, 201)

    claim = _participants(api_client, task_id, board["token"])[0]
    resp = api_client.patch(
        f"/tasks/{task_id}/participants/{claim['id']}",
        json={"approved": "DISAPPROVED"},
        headers=_auth(board["token"]),
    )
    assert resp.status_code == 200, resp.text

    assert _claim_as_team(api_client, task_id, second, token).status_code in (200, 201)
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
