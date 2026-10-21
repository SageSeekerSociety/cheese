"""Ctrl+K finds 题目 in the spaces the person is in, as each space's list shows
them: published ones, in their own spaces only."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    unique_int,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _space(api_client: TestClient, token: str, name: str) -> int:
    resp = create_approved_space(
        api_client,
        json={
            "name": f"{name} ({unique_int(10000000, 99999999)})",
            "intro": name,
            "description": name,
            "avatarId": 1,
            "taskTemplates": [],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["space"]["id"]


def _task(api_client: TestClient, token: str, space: int, name: str, approve=True):
    resp = api_client.post(
        "/tasks",
        headers=_auth(token),
        json={
            "name": name,
            "intro": name,
            "description": name,
            "space": space,
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": int((time.time() + 7 * 86400) * 1000),
        },
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    if approve:
        resp = api_client.patch(
            f"/tasks/{task_id}", headers=_auth(token), json={"approved": "APPROVED"}
        )
        assert resp.status_code == 200, resp.text
    return task_id


def test_the_palette_finds_published_tasks_in_my_spaces_only(
    user_client: UserCreator, api_client: TestClient
):
    teacher = user_client.create_user()
    teacher_token = user_client.login(api_client, teacher.username, teacher.password)
    student = user_client.create_user()
    token = user_client.login(api_client, student.username, student.password)
    mine = _space(api_client, teacher_token, "计算机系统")
    other = _space(api_client, teacher_token, "别的课")
    resp = api_client.post(
        f"/spaces/{mine}/members",
        json={"userId": student.user_id},
        headers=_auth(teacher_token),
    )
    assert resp.status_code == 201, resp.text

    _task(api_client, teacher_token, mine, "缓存模拟器实验")
    _task(api_client, teacher_token, mine, "缓存一致性（未审核）", approve=False)
    _task(api_client, teacher_token, other, "缓存替换策略")

    resp = api_client.get("/tasks/search?q=缓存", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    found = resp.json()["data"]["tasks"]
    assert [t["name"] for t in found] == ["缓存模拟器实验"]
    assert found[0]["spaceId"] == mine

    empty = api_client.get("/tasks/search?q=", headers=_auth(token))
    assert empty.json()["data"]["tasks"] == []
