"""分析接口的 ``sortBy`` 默认值必须是它自己白名单里的键。

``GET /spaces/{spaceId}/analytics/tasks`` 曾经把查询参数默认成
``publishedAt``，而这一层的排序实现（``SpaceAnalyticsViewService.get_tasks``）
用的是 ``TASK_SORT_FIELDS`` —— 那个集合里没有 ``publishedAt``，于是
``_normalize_task_sort_by`` 见到默认值就 ``raise BadRequestError``：**照文档
默认调用必定 400**。``/tasks`` 那条路之所以没事，是它先把 ``publishedAt``
映射成 ``createdAt`` 才进排序；这一层从来没有那步映射。

这份文件盯的是「按默认值调用」这一条最自然的调用方式，以及同一个房间内其它
分析接口的默认值有没有同类毛病（它们各自的默认值同样得落在自己的白名单里）。
"""

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


def _new_board(user_client: UserCreator, api_client: TestClient) -> dict:
    """一个管理员 + 一块已通过审核的题目板。管理员是建版的人。"""
    creator = user_client.create_user()
    token = user_client.login(api_client, creator.username, creator.password)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Analytics Sort Defaults ({unique_int(10000000, 99999999)})",
            "intro": "一门课",
            "description": "一个题目板",
            "avatarId": 1,
            "announcements": [],
            "taskTemplates": [],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]
    return {
        "creator_token": token,
        "space_id": space["id"],
        "category_id": space.get("defaultCategoryId"),
    }


def _create_task(api_client: TestClient, board: dict, *, name: str) -> int:
    resp = api_client.post(
        "/tasks",
        json={
            "name": name,
            "intro": "本周作业",
            "description": '{"type":"doc","content":[]}',
            "space": board["space_id"],
            "categoryId": board["category_id"],
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": int(time.time() * 1000) + 7 * 86400 * 1000,
        },
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["task"]["id"]


def _task_ids(api_client: TestClient, board: dict, query: str = "") -> list[int]:
    resp = api_client.get(
        f"/spaces/{board['space_id']}/analytics/tasks{query}",
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, (query, resp.status_code, resp.text)
    return [row["taskId"] for row in resp.json()["data"]["tasks"]]


def test_the_task_analytics_endpoint_takes_its_own_default_sort(
    api_client: TestClient, user_client: UserCreator
):
    """不传 ``sortBy`` → 200，且行为就是 ``sortBy=createdAt``。

    把默认值改回 ``publishedAt`` 这一条必红：请求会变成 400，断言在
    ``_task_ids`` 里就炸了（``query`` 为空串，也就是「按文档默认调用」）。
    """
    board = _new_board(user_client, api_client)
    first = _create_task(api_client, board, name="先出的题")
    second = _create_task(api_client, board, name="后出的题")

    # 默认调用：不带任何查询参数。
    default_ids = _task_ids(api_client, board)
    assert set(default_ids) == {first, second}

    # 默认值不是「任意能过」的键，而是 createdAt：显式传同样的排序结果一模一样。
    assert default_ids == _task_ids(
        api_client, board, "?sortBy=createdAt&sortOrder=desc"
    )

    # desc 的意思也真的成立：返回行按 createdAt 降序。
    resp = api_client.get(
        f"/spaces/{board['space_id']}/analytics/tasks",
        headers=_auth(board["creator_token"]),
    )
    created = [row["createdAt"] for row in resp.json()["data"]["tasks"]]
    assert created == sorted(created, reverse=True)

    # 显式传 sortBy 的老调用方一字不变：合法键照旧 200，非法键照旧 400。
    assert (
        _task_ids(api_client, board, "?sortBy=createdAt&sortOrder=asc")
        == default_ids[::-1]
    )
    bad = api_client.get(
        f"/spaces/{board['space_id']}/analytics/tasks?sortBy=publishedAt",
        headers=_auth(board["creator_token"]),
    )
    assert bad.status_code == 400, bad.text


def test_the_other_analytics_defaults_are_their_own_legal_keys(
    api_client: TestClient, user_client: UserCreator
):
    """同一房间内其它分析接口「按默认值调用」也必须 200。

    publishers 的默认 ``taskCount``、participants 的默认 ``joinedAt``、overview
    的默认 ``groupBy=day``、以及三条导出的默认参数，各自的默认值都得落在自己的
    白名单里 —— 和 tasks 那条曾经栽的是同一个坑。
    """
    board = _new_board(user_client, api_client)
    _create_task(api_client, board, name="一个题")

    for suffix in (
        "/analytics/tasks",
        "/analytics/tasks/export",
        "/analytics/publishers",
        "/analytics/publishers/export",
        "/analytics/participants",
        "/analytics/participants/export",
        "/analytics/overview",
        "/analytics/alerts",
    ):
        resp = api_client.get(
            f"/spaces/{board['space_id']}{suffix}",
            headers=_auth(board["creator_token"]),
        )
        assert resp.status_code == 200, (suffix, resp.status_code, resp.text)
