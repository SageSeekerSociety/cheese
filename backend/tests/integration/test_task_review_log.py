"""一道题「谁审的、什么时候审的」—— 审核痕迹读出来是什么。

审题走 ``PATCH /tasks/{id}``，判据只有 ``approved`` 与 ``reject_reason`` 两格；
``updated_at`` 每次 PATCH 都刷（改标题、改截止也刷），所以它顶不了审核时间。审核页
那张「最近处理过」要的正是这件事，于是题目表多了 ``reviewed_by`` / ``reviewed_at``。

这里按 HTTP 上能观察到的来断，不看那两列怎么存：

1. 审过之后 ``GET /tasks`` 报的是**审核人**与**那一刻**，不是出题人；
2. **改标题不动 ``reviewedAt``**，而 ``updatedAt`` 会动 —— 两条时间不是同一条（这条
   是整件事的理由：拿 ``updatedAt`` 当审核时间，会把「上个月改过简介」说成「刚刚
   审过」）；
3. 驳回后作者改完重新提交、换个人再审，那两格覆盖成最后一次；
4. ``sort_by=reviewedAt`` 收得下，而且没审过的题排在后面（这一列对老题是 null）。
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


def _login(user_client: UserCreator, api_client: TestClient, user) -> str:
    return user_client.login(api_client, user.username, user.password)


def _new_board(user_client: UserCreator, api_client: TestClient) -> dict:
    """一块已过审的板 + 建它的人（OWNER，管理员）。"""
    owner = user_client.create_user()
    owner_token = _login(user_client, api_client, owner)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Review Log ({suffix})",
            "intro": "一门课",
            "description": "一个题目板",
            "avatarId": 1,
            "taskTemplates": [],
        },
        headers=_auth(owner_token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]
    return {
        "owner": owner,
        "owner_token": owner_token,
        "space_id": space["id"],
        "category_id": space.get("defaultCategoryId"),
    }


def _make_admin(api_client: TestClient, board: dict, user) -> None:
    """再放一位管理员进这块板 —— 审核人必须可能不是出题人，用例才测得出区别。"""
    resp = api_client.post(
        f"/spaces/{board['space_id']}/managers",
        json={"userId": user.user_id, "role": "ADMIN"},
        headers=_auth(board["owner_token"]),
    )
    assert resp.status_code == 201, resp.text


def _publish(api_client: TestClient, board: dict, *, name: str) -> dict:
    resp = api_client.post(
        "/tasks",
        json={
            "name": name,
            "intro": "题",
            "description": '{"type":"doc","content":[]}',
            "space": board["space_id"],
            "categoryId": board["category_id"],
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
        },
        headers=_auth(board["owner_token"]),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["task"]


def _review(
    api_client: TestClient,
    task_id: int,
    token: str,
    *,
    approved: str,
    reason: str = "",
) -> dict:
    payload: dict = {"approved": approved}
    if reason:
        payload["rejectReason"] = reason
    resp = api_client.patch(f"/tasks/{task_id}", json=payload, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["task"]


def _from_list(api_client: TestClient, board: dict, task_id: int, token: str) -> dict:
    """从**列表**接口取这道题 —— 审核页读的就是这一条，不是详情那一条。"""
    resp = api_client.get(
        "/tasks",
        params={"space": board["space_id"], "pageSize": 100},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    for task in resp.json()["data"]["tasks"]:
        if task["id"] == task_id:
            return task
    raise AssertionError(f"task {task_id} is not in the list response")


def test_the_list_reports_the_reviewer_and_the_moment(
    api_client: TestClient, user_client: UserCreator
):
    """审核人是审核人，不是出题人；那一刻是审核那一刻。"""
    board = _new_board(user_client, api_client)
    reviewer = user_client.create_user()
    reviewer_token = _login(user_client, api_client, reviewer)
    _make_admin(api_client, board, reviewer)

    task = _publish(api_client, board, name="待审的题")
    before = int(time.time() * 1000)

    # 审之前两格都在，都是 null —— 界面上「没人审过」与「不知道谁审的」在这里分开。
    fresh = _from_list(api_client, board, task["id"], board["owner_token"])
    assert fresh["reviewedBy"] is None
    assert fresh["reviewedAt"] is None

    _review(api_client, task["id"], reviewer_token, approved="APPROVED")
    after = int(time.time() * 1000)

    reviewed = _from_list(api_client, board, task["id"], board["owner_token"])
    assert reviewed["approved"] == "APPROVED"
    # 审核人，而不是出题人（出题人是 board 的所有者）。
    assert reviewed["reviewedBy"] == reviewer.user_id
    assert reviewed["reviewedBy"] != board["owner"].user_id
    # 那一刻落在这次 PATCH 的前后之间（给一点时钟容差）。
    assert before - 1000 <= reviewed["reviewedAt"] <= after + 1000


def test_editing_the_title_does_not_move_the_review_time(
    api_client: TestClient, user_client: UserCreator
):
    """改标题刷 ``updatedAt``，但不动 ``reviewedAt`` —— 两条时间不是同一条。

    这条就是加那两列的理由：老接口的 ``updatedAt`` 会被任何一次编辑污染，所以它
    既不能说「什么时候审的」，也不能用来排「最近处理过」。
    """
    board = _new_board(user_client, api_client)
    task = _publish(api_client, board, name="一开始的名字")
    _review(api_client, task["id"], board["owner_token"], approved="APPROVED")

    approved_once = _from_list(api_client, board, task["id"], board["owner_token"])
    # 先确认这一次审核**真的留了痕**，下面那句「没动」才不是 null == null。
    assert approved_once["reviewedBy"] == board["owner"].user_id
    assert approved_once["reviewedAt"] is not None

    # 隔开一点再改，两个毫秒时间戳才分得开。
    time.sleep(0.02)
    rename = api_client.patch(
        f"/tasks/{task['id']}",
        json={"name": "改过的名字"},
        headers=_auth(board["owner_token"]),
    )
    assert rename.status_code == 200, rename.text

    renamed = _from_list(api_client, board, task["id"], board["owner_token"])
    assert renamed["name"] == "改过的名字"
    # 编辑动了 updatedAt ……
    assert renamed["updatedAt"] > approved_once["updatedAt"]
    # ……但审核那一刻纹丝不动。
    assert renamed["reviewedAt"] == approved_once["reviewedAt"]
    assert renamed["reviewedBy"] == approved_once["reviewedBy"]


def test_a_second_review_overwrites_the_first_one(
    api_client: TestClient, user_client: UserCreator
):
    """驳回 → 作者重提 → 换个人再审：读到的是最后一次那个人、那一刻。"""
    board = _new_board(user_client, api_client)
    first = user_client.create_user()
    first_token = _login(user_client, api_client, first)
    _make_admin(api_client, board, first)
    second = user_client.create_user()
    second_token = _login(user_client, api_client, second)
    _make_admin(api_client, board, second)

    task = _publish(api_client, board, name="会被驳回的题")

    _review(
        api_client,
        task["id"],
        first_token,
        approved="DISAPPROVED",
        reason="口径没写清",
    )
    rejected = _from_list(api_client, board, task["id"], board["owner_token"])
    assert rejected["approved"] == "DISAPPROVED"
    assert rejected["reviewedBy"] == first.user_id

    resubmit = api_client.post(
        f"/tasks/{task['id']}/resubmit", headers=_auth(board["owner_token"])
    )
    assert resubmit.status_code == 200, resubmit.text
    assert resubmit.json()["data"]["task"]["approved"] == "NONE"

    # 重新提交本身不是审核：痕迹还是上一次那个人，直到第二个人真的审了它。
    pending = _from_list(api_client, board, task["id"], board["owner_token"])
    assert pending["reviewedBy"] == first.user_id

    _review(api_client, task["id"], second_token, approved="APPROVED")

    # 一个"审核人"栏、不是一条审核日志：这一行说的是最后那次。
    finally_ = _from_list(api_client, board, task["id"], board["owner_token"])
    assert finally_["approved"] == "APPROVED"
    assert finally_["reviewedBy"] == second.user_id
    assert finally_["reviewedBy"] != first.user_id
    assert finally_["reviewedAt"] >= rejected["reviewedAt"]


def test_sorting_by_review_time_takes_the_parameter_and_puts_unreviewed_last(
    api_client: TestClient, user_client: UserCreator
):
    """``sortBy=reviewedAt`` 收得下；老题（这一列是 null）排在最后，不排在头上。

    Postgres 的 DESC 默认把 NULL 排在**最前**，于是「最近处理过」的第一屏会全是
    不知道谁审过的旧题。审核页就靠这一条口径。
    """
    board = _new_board(user_client, api_client)
    reviewed = _publish(api_client, board, name="刚审过的题")
    _review(api_client, reviewed["id"], board["owner_token"], approved="APPROVED")
    # 这道题在**那次审核之后**才发出来，发完不再碰它：它的 `updatedAt` 比那次审核
    # 更晚，所以「按 updatedAt 倒序排」会把它排到前面 —— 而这个顺序是错的，它的
    # `reviewedAt` 是 null，按审核时间排必须落到后面。
    time.sleep(0.02)
    unreviewed = _publish(api_client, board, name="还在等审的题")

    resp = api_client.get(
        "/tasks",
        params={
            "space": board["space_id"],
            "pageSize": 100,
            "sort_by": "reviewedAt",
            "sort_order": "desc",
        },
        headers=_auth(board["owner_token"]),
    )
    assert resp.status_code == 200, resp.text
    ids = [task["id"] for task in resp.json()["data"]["tasks"]]
    assert ids == [reviewed["id"], unreviewed["id"]]
