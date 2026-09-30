"""整板分析的题目那一条要带出每题的名额上限。

看板上的「领取 3 / 5」这一截，分子来自 ``participantCount``，分母就是这里新加的
``participantLimit``。这一列与题目自身的 ``participant_limit`` 是同一条，不是推导出
来的：没设上限的题在库里是 ``NULL``，接口给出的也必须是 ``null``（而不是 0）——
0 在表里会画成「/ 0」，读起来像是这道题一个人都不许领。

断言落在接口返回的字段上：设了上限的题读出那个数，没设的读出 ``None``。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    unique_int,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _publish_approved_task(
    api_client: TestClient,
    creator_token: str,
    *,
    space_id: int,
    category_id: int,
    participant_limit: int | None,
) -> int:
    deadline = int((datetime.now(UTC) + timedelta(days=7)).timestamp() * 1000)
    body: dict = {
        "name": f"Analytics Limit Task ({unique_int(1000, 9999)})",
        "intro": "题",
        "description": '{"type":"doc","content":[]}',
        "submitterType": "USER",
        "resubmittable": True,
        "editable": True,
        "defaultDeadline": 30,
        "deadline": deadline,
        "space": space_id,
        "categoryId": category_id,
    }
    if participant_limit is not None:
        body["participantLimit"] = participant_limit
    resp = api_client.post("/tasks", json=body, headers=_auth(creator_token))
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    approved = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(creator_token),
    )
    assert approved.status_code == 200, approved.text
    return task_id


def test_the_task_table_carries_the_participant_limit_or_null(
    api_client: TestClient, user_client: UserCreator
):
    """设了上限的题读出那个数，没设的读出 null（不是 0）。"""
    creator = user_client.create_user()
    token = user_client.login(api_client, creator.username, creator.password)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Analytics Task Limit ({unique_int(10000000, 99999999)})",
            "intro": "一门课",
            "description": "一块题目板",
            "avatarId": 1,
            "taskTemplates": [],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]

    limited = _publish_approved_task(
        api_client,
        token,
        space_id=space["id"],
        category_id=space["defaultCategoryId"],
        participant_limit=5,
    )
    unlimited = _publish_approved_task(
        api_client,
        token,
        space_id=space["id"],
        category_id=space["defaultCategoryId"],
        participant_limit=None,
    )

    rows = api_client.get(
        f"/spaces/{space['id']}/analytics/tasks",
        params={"sortBy": "createdAt"},
        headers=_auth(token),
    )
    assert rows.status_code == 200, rows.text
    by_id = {row["taskId"]: row for row in rows.json()["data"]["tasks"]}

    assert by_id[limited]["participantLimit"] == 5, by_id[limited]
    assert by_id[unlimited]["participantLimit"] is None, by_id[unlimited]
