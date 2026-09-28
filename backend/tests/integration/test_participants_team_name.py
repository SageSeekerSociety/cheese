"""领取名单要说得出是**哪支队伍**领的：`GET /tasks/{taskId}/participants` 团队领取的
那一条带上 `team: {id, name}`。

单题看板（`frontend/src/views/spaces/board/pages/TaskInsights.vue`）的「小队构成」
按队名分桶、名册里写出队名。这条接口以前只给 `isTeam`、不给队名，于是几支队伍全挤在
「小队」同一个桶里 —— 出题人看得出「有队伍来了」，看不出「是哪几支」。本文件钉三件事：

1. 团队领取的那一条带**对**的队名（拿别人队的名字这条就红）；
2. 按人领取的那一条**没有** `team` 字段（把个人也塞进团队桶会在这里露馅）；
3. 这道门没被放宽：无关的登录用户照旧 403。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import (
    CreatedUser,
    UserCreator,
    create_approved_space,
    unique_int,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(user_client: UserCreator, api_client: TestClient, user: CreatedUser) -> str:
    return user_client.login(api_client, user.username, user.password)


@pytest.fixture
def board(user_client: UserCreator, api_client: TestClient) -> dict:
    """一块已批准的板，出题人是建版的人。"""
    creator = user_client.create_user()
    creator_token = _login(user_client, api_client, creator)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Participants Team Name ({suffix})",
            "intro": "一块板",
            "description": "领取名单里的队名",
            "avatarId": 1,
            "announcements": [],
            "taskTemplates": [],
        },
        headers=_auth(creator_token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]
    return {
        "creator": creator,
        "creator_token": creator_token,
        "space_id": space["id"],
        "category_id": space.get("defaultCategoryId"),
    }


def _member_of(
    user_client: UserCreator, api_client: TestClient, board: dict
) -> tuple[CreatedUser, str]:
    """板上的人 —— 看得见这块板，但既不是管理员也不是出题人。"""
    user = user_client.create_user()
    token = _login(user_client, api_client, user)
    resp = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": user.user_id},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 201, resp.text
    return user, token


def _create_task(
    api_client: TestClient, board: dict, *, name: str, submitter_type: str
) -> int:
    resp = api_client.post(
        "/tasks",
        json={
            "name": name,
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
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text
    return task_id


def _participants(api_client: TestClient, task_id: int, token: str) -> list[dict]:
    resp = api_client.get(f"/tasks/{task_id}/participants", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["participants"]


@pytest.fixture
def stage(api_client: TestClient, user_client: UserCreator, board: dict) -> dict:
    """板上两道题：一道团队题上有一支队伍领了，一道个人题上有一个人领了。"""
    team_owner, team_owner_token = _member_of(user_client, api_client, board)

    team_name = f"玄武队 {unique_int(10000000, 99999999)}"
    team_resp = api_client.post(
        "/teams",
        json={
            "name": team_name,
            "intro": "一支队伍",
            "description": "为了这道题组的队",
            "avatarId": 1,
        },
        headers=_auth(team_owner_token),
    )
    assert team_resp.status_code == 201, team_resp.text
    team_id = team_resp.json()["data"]["team"]["id"]

    solo, solo_token = _member_of(user_client, api_client, board)
    _, outsider_token = _member_of(user_client, api_client, board)

    team_task_id = _create_task(api_client, board, name="团队题", submitter_type="TEAM")
    join = api_client.post(
        f"/tasks/{team_task_id}/participations/team",
        json={"teamId": team_id},
        headers=_auth(team_owner_token),
    )
    assert join.status_code in (200, 201), join.text

    solo_task_id = _create_task(api_client, board, name="个人题", submitter_type="USER")
    join = api_client.post(
        f"/tasks/{solo_task_id}/participations/user",
        json={},
        headers=_auth(solo_token),
    )
    assert join.status_code in (200, 201), join.text

    return {
        "board": board,
        "team_id": team_id,
        "team_name": team_name,
        "team_owner_token": team_owner_token,
        "solo": solo,
        "solo_token": solo_token,
        "outsider_token": outsider_token,
        "team_task_id": team_task_id,
        "solo_task_id": solo_task_id,
    }


def test_a_team_registration_carries_its_own_team_name(
    api_client: TestClient, stage: dict
):
    row = next(
        p
        for p in _participants(
            api_client, stage["team_task_id"], stage["board"]["creator_token"]
        )
        if p["memberId"] == stage["team_id"]
    )

    assert row["isTeam"] is True
    assert row["team"] == {"id": stage["team_id"], "name": stage["team_name"]}


def test_an_individual_registration_has_no_team(api_client: TestClient, stage: dict):
    """按人领的那一条是说不出队名的 —— 它压根不是一支队伍。"""
    row = next(
        p
        for p in _participants(
            api_client, stage["solo_task_id"], stage["board"]["creator_token"]
        )
        if p["memberId"] == stage["solo"].user_id
    )

    assert row["isTeam"] is False
    assert "team" not in row


def test_an_unrelated_signed_in_user_still_cannot_read_the_roster(
    api_client: TestClient, stage: dict
):
    """这次只加字段，门没动：板上无关的人照旧 403（判据与单条那条路由同一条）。"""
    resp = api_client.get(
        f"/tasks/{stage['team_task_id']}/participants",
        headers=_auth(stage["outsider_token"]),
    )
    assert resp.status_code == 403, resp.text
    # 队名也不能从这句话里漏出去。
    assert stage["team_name"] not in resp.text
