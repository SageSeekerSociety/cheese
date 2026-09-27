"""报名者联系方式：单条那条路由的门，与列表版**同一条判据**。

`GET /tasks/{taskId}/participants/{participantId}`（`routes/tasks.py`）里
`auth_user` 被 `_ = auth_user` 丢掉，函数体里没有一句授权 —— 返回体却带着
`email` / `phone`（`_membership_to_api_model`）。同一条路由的**列表版**
（`GET /{taskId}/participants`）有门：403 "Only task owner or space admin can
view participants"。member id 是小整数、可枚举，所以漏的这一侧等于把报名者填在
报名表上的联系方式交给任何登录用户。

本文件按浏览器会收到的状态码逐条断，判据一律照抄隔壁列表版，不另立口径：

1. 无关的登录用户 → **403**（不是 404）。理由：同一个调用者在同一个资源上，列表版
   已经用 403 回答了「你看不了这份名单」；这里换 404 会变成另一句话（「这道题上没有
   这个人」），而调用者早就知道这个人存在 —— 两个口径对不上，也正是审计要修的那种
   不一致。匿名 → 401（`require_auth_user`）。
2. 出题人与板管理员 → 200，且 `email` / `phone` 照旧在返回体里。这道门挡的是
   「无关的人」，不是「联系方式」本身：能管这道题的人本来就在参与名单里看得到它们。
3. 另一道题上的 `participantId` → 404：`membership.task_id != task_id` 那条绑定
   是既有行为，这次改动不该碰松它。
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

# 报名者填在报名表上的东西。审计用它证明「无关的人也能读到」——测试里同样用具体
# 字符串而不是字段名存在性：字段在、内容是别人的，才是要被挡住的那件事。
VICTIM_EMAIL = "victim-private@example.com"
VICTIM_PHONE = "13800000000"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(user_client: UserCreator, api_client: TestClient, user: CreatedUser) -> str:
    return user_client.login(api_client, user.username, user.password)


@pytest.fixture
def board(user_client: UserCreator, api_client: TestClient) -> dict:
    """一块已批准的板，管理者是建版的人。"""
    creator = user_client.create_user()
    creator_token = _login(user_client, api_client, creator)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Participant Read Authz ({suffix})",
            "intro": "一块板",
            "description": "报名者联系方式",
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


def _create_task(api_client: TestClient, board: dict, *, name: str) -> int:
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


def _claim(api_client: TestClient, task_id: int, token: str) -> int:
    """报名并留下联系方式 —— 审计里那两个字段就是从这一步进库的。"""
    resp = api_client.post(
        f"/tasks/{task_id}/participations/user",
        json={"email": VICTIM_EMAIL, "phone": VICTIM_PHONE},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return int(resp.json()["data"]["participant"]["id"])


def _single(api_client: TestClient, task_id: int, participant_id: int, token: str | None):
    return api_client.get(
        f"/tasks/{task_id}/participants/{participant_id}",
        headers=_auth(token) if token else {},
    )


# --- 该挡的 -------------------------------------------------------------------


def test_an_unrelated_signed_in_user_cannot_read_a_participants_contact_info(
    api_client: TestClient, user_client: UserCreator, board: dict
):
    """审计那条路径：出题人建题 → 报名者留下 email/phone → 无关的第三个人仅登录。"""
    task_id = _create_task(api_client, board, name="报名表上有联系方式")
    _, applicant_token = _member_of(user_client, api_client, board)
    participant_id = _claim(api_client, task_id, applicant_token)

    _, outsider_token = _member_of(user_client, api_client, board)
    resp = _single(api_client, task_id, participant_id, outsider_token)
    assert resp.status_code == 403, resp.text
    assert VICTIM_EMAIL not in resp.text
    assert VICTIM_PHONE not in resp.text


def test_an_anonymous_caller_cannot_read_a_participants_contact_info(
    api_client: TestClient, user_client: UserCreator, board: dict
):
    task_id = _create_task(api_client, board, name="匿名也要挡")
    _, applicant_token = _member_of(user_client, api_client, board)
    participant_id = _claim(api_client, task_id, applicant_token)

    resp = _single(api_client, task_id, participant_id, None)
    assert resp.status_code == 401, resp.text
    assert VICTIM_EMAIL not in resp.text


# --- 该放的 -------------------------------------------------------------------


def test_the_publisher_still_reads_the_participant(
    api_client: TestClient, user_client: UserCreator, board: dict
):
    """出题人：列表版给他看，单条版也得给他看 —— 同一条判据，同一个答案。"""
    task_id = _create_task(api_client, board, name="出题人看得到")
    applicant, applicant_token = _member_of(user_client, api_client, board)
    participant_id = _claim(api_client, task_id, applicant_token)

    resp = _single(api_client, task_id, participant_id, board["creator_token"])
    assert resp.status_code == 200, resp.text
    participant = resp.json()["data"]["participant"]
    assert participant["id"] == participant_id
    assert participant["memberId"] == applicant.user_id
    assert participant["email"] == VICTIM_EMAIL
    assert participant["phone"] == VICTIM_PHONE


def test_a_board_manager_still_reads_the_participant(
    api_client: TestClient, user_client: UserCreator, board: dict
):
    """管理员是替出题人管这块板的人 —— 与列表版同一条主张。"""
    task_id = _create_task(api_client, board, name="管理员看得到")
    _, applicant_token = _member_of(user_client, api_client, board)
    participant_id = _claim(api_client, task_id, applicant_token)

    manager = user_client.create_user()
    manager_token = _login(user_client, api_client, manager)
    resp = api_client.post(
        f"/spaces/{board['space_id']}/managers",
        json={"userId": manager.user_id, "role": "ADMIN"},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 201, resp.text

    resp = _single(api_client, task_id, participant_id, manager_token)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["participant"]["email"] == VICTIM_EMAIL


def test_a_participant_from_another_task_is_still_not_found(
    api_client: TestClient, user_client: UserCreator, board: dict
):
    """`membership.task_id != task_id` 那条绑定没被碰松：换个 taskId 找不到这张报名表。"""
    with_participant = _create_task(api_client, board, name="报名表在这道题上")
    empty = _create_task(api_client, board, name="这道题上没有这张报名表")
    _, applicant_token = _member_of(user_client, api_client, board)
    participant_id = _claim(api_client, with_participant, applicant_token)

    resp = _single(api_client, empty, participant_id, board["creator_token"])
    assert resp.status_code == 404, resp.text
