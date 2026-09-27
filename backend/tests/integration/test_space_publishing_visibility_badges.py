"""发布者自己的「我发布的题目」里的可见性徽章，必须与看板说的是同一件事。

空间可以设「每个发布者对普通用户可见的未结项已通过题目数量上限」
（``space.visible_task_limit``，上限 M）。看板按 ``coalesce(published_at, created_at),
id`` 在每个创作者内部排名，只放前 M 道。发布者页那一列徽章回答的正是「这道题在不在
那前 M 道里」。

一度它拿**已被这一页的 categoryId / approved / from / to 筛过**的列表去排名。于是
同一个分类筛选就能把同一道题的徽章从「已通过但隐藏」翻成「已通过且可见」——而这道题
在题目板上领取者根本看不到。上限是空间的性质，不是这一页筛选条件的性质，所以排名必须
在**不经筛选**的那一份上做。

断言的是接口返回的 ``visibilityStatus`` / ``isVisible`` 与看板列出的题目 id。
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


def _login(user_client: UserCreator, api_client: TestClient, user) -> str:
    return user_client.login(api_client, user.username, user.password)


def _space(api_client: TestClient, creator_token: str) -> dict:
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Visibility Badge ({suffix})",
            "intro": "一门课",
            "description": "一块题目板",
            "avatarId": 1,
            "announcements": [],
            "taskTemplates": [],
        },
        headers=_auth(creator_token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["space"]


def _set_visible_task_limit(
    api_client: TestClient, creator_token: str, *, space_id: int, limit: int
) -> None:
    resp = api_client.patch(
        f"/spaces/{space_id}",
        json={"visibleTaskLimit": limit},
        headers=_auth(creator_token),
    )
    assert resp.status_code == 200, resp.text


def _category(api_client: TestClient, creator_token: str, *, space_id: int) -> int:
    resp = api_client.post(
        f"/spaces/{space_id}/categories",
        json={"name": f"Category {unique_int(1000, 9999)}"},
        headers=_auth(creator_token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["category"]["id"]


def _publish_approved_task(
    api_client: TestClient,
    creator_token: str,
    *,
    space_id: int,
    category_id: int,
) -> int:
    """一道已通过、未结项的题。"""
    deadline = int((datetime.now(UTC) + timedelta(days=7)).timestamp() * 1000)
    resp = api_client.post(
        "/tasks",
        json={
            "name": f"Visibility Task ({unique_int(1000, 9999)})",
            "intro": "题",
            "description": '{"type":"doc","content":[]}',
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": deadline,
            "space": space_id,
            "categoryId": category_id,
        },
        headers=_auth(creator_token),
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    approved = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(creator_token),
    )
    assert approved.status_code == 200, approved.text
    return task_id


def _my_publishing_tasks(
    api_client: TestClient,
    token: str,
    *,
    space_id: int,
    category_id: int | None = None,
) -> dict[int, dict]:
    params = {} if category_id is None else {"categoryId": category_id}
    resp = api_client.get(
        f"/spaces/{space_id}/me/publishing/tasks", params=params, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    return {row["taskId"]: row for row in resp.json()["data"]["tasks"]}


def _board_task_ids(api_client: TestClient, token: str, *, space_id: int) -> set[int]:
    """看板给这个普通成员列出的题目 —— 徽章说什么，这里就必须是什么。"""
    resp = api_client.get("/tasks", params={"space": space_id}, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return {task["id"] for task in resp.json()["data"]["tasks"]}


def _ordinary_member(
    api_client: TestClient, user_client: UserCreator, *, space_id: int, owner_token: str
) -> str:
    member = user_client.create_user()
    member_token = _login(user_client, api_client, member)
    added = api_client.post(
        f"/spaces/{space_id}/members",
        json={"userId": member.user_id},
        headers=_auth(owner_token),
    )
    assert added.status_code == 201, added.text
    return member_token


def test_a_category_filter_does_not_flip_a_hidden_badge(
    api_client: TestClient, user_client: UserCreator
):
    """上限 1、两道已通过的题（各占一个分类）：只有先发的那道可见。

    带上后发那道题的分类再问一次，它仍然是「已通过但隐藏」—— 筛选只是这一页少放
    几行，不能让一道题在徽章上越过看板的上限。
    """
    creator = user_client.create_user()
    creator_token = _login(user_client, api_client, creator)
    space = _space(api_client, creator_token)
    space_id = space["id"]
    category_a = space["defaultCategoryId"]
    category_b = _category(api_client, creator_token, space_id=space_id)

    _set_visible_task_limit(api_client, creator_token, space_id=space_id, limit=1)

    first = _publish_approved_task(
        api_client, creator_token, space_id=space_id, category_id=category_a
    )
    second = _publish_approved_task(
        api_client, creator_token, space_id=space_id, category_id=category_b
    )

    member_token = _ordinary_member(
        api_client, user_client, space_id=space_id, owner_token=creator_token
    )
    board = _board_task_ids(api_client, member_token, space_id=space_id)
    assert first in board and second not in board, (
        "前提没成立：上限 1 时看板只放先发的那一道"
    )

    # 不带筛选时，徽章与看板一致。
    rows = _my_publishing_tasks(api_client, creator_token, space_id=space_id)
    assert rows[first]["isVisible"] is True, rows[first]
    assert rows[first]["visibilityStatus"] == "APPROVED_VISIBLE", rows[first]
    assert rows[second]["isVisible"] is False, rows[second]
    assert rows[second]["visibilityStatus"] == "APPROVED_HIDDEN", rows[second]

    # 只筛第二个分类：这一页只剩后发的那道，它的徽章不许因此翻成可见。
    filtered = _my_publishing_tasks(
        api_client, creator_token, space_id=space_id, category_id=category_b
    )
    assert list(filtered) == [second], filtered
    assert filtered[second]["isVisible"] is False, filtered[second]
    assert filtered[second]["visibilityStatus"] == "APPROVED_HIDDEN", filtered[second]


def test_a_limit_of_zero_hides_every_approved_task(
    api_client: TestClient, user_client: UserCreator
):
    """上限 0 = 一道都不给普通用户看：徽章全隐藏，看板也确实一道都不列。"""
    creator = user_client.create_user()
    creator_token = _login(user_client, api_client, creator)
    space = _space(api_client, creator_token)
    space_id = space["id"]

    _set_visible_task_limit(api_client, creator_token, space_id=space_id, limit=0)
    first = _publish_approved_task(
        api_client,
        creator_token,
        space_id=space_id,
        category_id=space["defaultCategoryId"],
    )
    second = _publish_approved_task(
        api_client,
        creator_token,
        space_id=space_id,
        category_id=space["defaultCategoryId"],
    )

    member_token = _ordinary_member(
        api_client, user_client, space_id=space_id, owner_token=creator_token
    )
    assert _board_task_ids(api_client, member_token, space_id=space_id) == set()

    rows = _my_publishing_tasks(api_client, creator_token, space_id=space_id)
    for task_id in (first, second):
        assert rows[task_id]["isVisible"] is False, rows[task_id]
        assert rows[task_id]["visibilityStatus"] == "APPROVED_HIDDEN", rows[task_id]
