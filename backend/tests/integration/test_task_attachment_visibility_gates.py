"""题目的附件清单，不许比题目本身更看得见。

`GET /tasks/{taskId}` 对「这道题存不存在于这个读者眼里」有两道闸，是附件那两条
路由一度没有走的：

1. **没通过审批的题**（``approved == 2``）：对出题人和板管理员是草稿，对其他人
   还不该存在 —— 403（``get_task`` 的原文口径）；
2. **超出本板上限**（``space.visible_task_limit``）：它已经是一道普通成员看不见的
   题了，再问就是 404，与「这道题不存在」同一句话。

附件路由当时只问了 ``TaskVisibilityService.can_view_task``，而那个判据在
``task.access_control_enabled`` 为假（题目默认值）时对任何登录用户都放行 —— 于是
一道 403 或 404 的题，仍然把它的材料清单（文件名、大小、上传者 id、下载次数）
交出去，题目 id 又是可猜的小整数，可以直接遍历。

本文件断的是「题目与它的清单同一个答案」：

- 未审批题：成员与陌生人都 403，出题人与板管理员照旧 200；
- 超出上限的题：成员 404，出题人照旧 200（他不受上限约束）；
- 两条路由（清单 / 下载）都要同调一个判据，不能清单 403 而下载 200。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import CreatedUser, UserCreator
from tests.integration.test_task_attachments import (
    _approve_task,
    _auth,
    _create_task,
    _list,
    _login,
    _new_board,
    _upload_to_task,
    upload_root,
)


@pytest.fixture
def stranger(user_client: UserCreator, api_client: TestClient) -> tuple[CreatedUser, str]:
    """一个登录了、但不在这块板上的真人。

    ``require_auth_user`` 要的是数字 user id，所以「陌生人」在这里必须是个真注册
    用户 —— 而「不是成员」正是他被要求的那件事：不给他 ``/spaces/{id}/members``。
    """
    user = user_client.create_user()
    return user, _login(user_client, api_client, user)


def _detail(api_client: TestClient, task_id: int, token: str):
    return api_client.get(f"/tasks/{task_id}", headers=_auth(token))


def test_an_unapproved_task_does_not_hand_out_its_list_to_a_board_member(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    """未审批的题对普通成员是 403（``get_task`` 的口径），清单也一样。

    清单不比这道题更公开：材料名本身常常就是答案（「期末考试答案.pdf」），而
    「有没有材料」在这里恰好又是「这道草稿题存不存在」的探针。
    """
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="还没过审的题")
    attached = _upload_to_task(api_client, task_id, board["creator_token"])
    member = user_client.create_user()
    member_token = _login(user_client, api_client, member)
    added = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": member.user_id},
        headers=_auth(board["creator_token"]),
    )
    assert added.status_code == 201, added.text

    # 前提：这道题本身对成员就是 403。
    assert _detail(api_client, task_id, member_token).status_code == 403

    status, _ = _list(api_client, task_id, member_token)
    assert status == 403, "清单跟着题目走：题是 403，清单不许 200"

    # 出题人是这道题的作者，两道门都为他开着。
    assert _detail(api_client, task_id, board["creator_token"]).status_code == 200
    status, body = _list(api_client, task_id, board["creator_token"])
    assert status == 200
    assert [a["id"] for a in body["data"]["attachments"]] == [attached["id"]]


def test_a_stranger_gets_nothing_from_an_unapproved_task(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,
    stranger: tuple[CreatedUser, str],
):
    """陌生人连这块板的成员都不是，却一度拿得到草稿题的材料清单。"""
    _, stranger_token = stranger
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="草稿题")
    _upload_to_task(api_client, task_id, board["creator_token"])

    assert _detail(api_client, task_id, stranger_token).status_code == 403
    status, _ = _list(api_client, task_id, stranger_token)
    assert status == 403, "不是这块板的人，连草稿题的名字都不该从材料清单里读出来"


def test_a_task_over_the_board_limit_hides_its_list_too(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    """上限 1、两道已通过的题：后发的那道对成员已经是 404，清单也得是 404。

    这类题的 ``get_task`` 与「不存在」同一句话，所以附件那条路也不能拿 403 说出
    另一句话 —— 403 与 404 的差别本身就是「这道题到底存不存在」的答案。
    """
    board = _new_board(user_client, api_client)
    first = _create_task(api_client, board, name="先发的题")
    second = _create_task(api_client, board, name="后发的题")
    _approve_task(api_client, board, first)
    _approve_task(api_client, board, second)

    limit = api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"visibleTaskLimit": 1},
        headers=_auth(board["creator_token"]),
    )
    assert limit.status_code == 200, limit.text

    member = user_client.create_user()
    member_token = _login(user_client, api_client, member)
    added = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": member.user_id},
        headers=_auth(board["creator_token"]),
    )
    assert added.status_code == 201, added.text

    _upload_to_task(api_client, first, board["creator_token"])
    hidden_material = _upload_to_task(
        api_client,
        second,
        board["creator_token"],
        filename="压轴题材料.pdf",
    )

    # 前提：上限 1 时，成员看得见先发的那道、看不见后发的那道。
    assert _detail(api_client, first, member_token).status_code == 200
    assert _detail(api_client, second, member_token).status_code == 404

    status, body = _list(api_client, first, member_token)
    assert status == 200
    assert len(body["data"]["attachments"]) == 1

    status, _ = _list(api_client, second, member_token)
    assert status == 404, "超出上限的题对成员不存在，它的清单也不存在"
    assert (
        api_client.get(
            f"/tasks/{second}/attachments/{hidden_material['id']}/download",
            headers=_auth(member_token),
        ).status_code
        == 404
    )

    # 出题人不受上限约束，两道题与两道题的材料都还是他的。
    assert _detail(api_client, second, board["creator_token"]).status_code == 200
    status, body = _list(api_client, second, board["creator_token"])
    assert status == 200
    assert [a["id"] for a in body["data"]["attachments"]] == [hidden_material["id"]]
