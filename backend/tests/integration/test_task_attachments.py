"""题目的附件：谁挂得上、谁看得见、谁拿得到。

题目的材料不能比题目本身更保密，也不能比它更公开 —— 这三句话各自落到一个
可观测的后果上，本文件按浏览器会收到的状态码逐条断：

1. **挂得上 / 摘得下**：出题人本人，或这块板的所有者 / 管理员（``may_teach_task``）；
2. **看得见清单**：看得见这道题的人 —— 看不见材料就无从判断要不要领这道题；
3. **拿得到文件**：出题人 / 板管理员 / **已经领取的人**。领取者拿不到材料就没法
   做题，所以这条比「看得见」窄一格、又比「管得了」宽一格。

还有两条是「猜 id」这件事的后果，因为它们不是权限规则而是漏洞的形状：附件 id 是
可猜的连续整数，所以 (a) 别人的文件挂不到我的题上，(b) 别道题上的文件不能靠换个
``taskId`` 就取走。
"""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import storage as storage_module
from app.core.config import settings
from tests.integration.conftest import (
    CreatedUser,
    UserCreator,
    create_approved_space,
    unique_int,
)


@pytest.fixture
def upload_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把本地存储落到 tmp_path。

    ``get_storage_backend()`` 是模块级单例，第一次调用就把 base_path 定死 —— 所以
    光改 settings 不够，还得先把已经建好的那个丢掉。收尾也丢掉一次：留在那里的
    话，下一个测试拿到的是指向已经删掉的 tmp_path 的 backend。
    """
    monkeypatch.setattr(settings, "storage_local_path", str(tmp_path))
    storage_module._storage_backend = None
    yield tmp_path
    storage_module._storage_backend = None


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(user_client: UserCreator, api_client: TestClient, user: CreatedUser) -> str:
    return user_client.login(api_client, user.username, user.password)


def _new_board(user_client: UserCreator, api_client: TestClient) -> dict:
    creator = user_client.create_user()
    creator_token = _login(user_client, api_client, creator)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Task Attachments ({suffix})",
            "intro": "一个题目板",
            "description": "放题目和材料的地方",
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
    """一个在这块板上、但不是管理员也不是出题人的人。"""
    user = user_client.create_user()
    token = _login(user_client, api_client, user)
    resp = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": user.user_id},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 201, resp.text
    return user, token


def _post_task(
    api_client: TestClient,
    board: dict,
    *,
    name: str,
    token: str | None = None,
    attachment_ids: list[int] | None = None,
    access_control_enabled: bool = False,
):
    body: dict = {
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
        "accessControlEnabled": access_control_enabled,
    }
    if attachment_ids is not None:
        body["attachmentIds"] = attachment_ids
    return api_client.post(
        "/tasks", json=body, headers=_auth(token or board["creator_token"])
    )


def _create_task(
    api_client: TestClient,
    board: dict,
    *,
    name: str,
    token: str | None = None,
    attachment_ids: list[int] | None = None,
    access_control_enabled: bool = False,
) -> int:
    resp = _post_task(
        api_client,
        board,
        name=name,
        token=token,
        attachment_ids=attachment_ids,
        access_control_enabled=access_control_enabled,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["task"]["id"]


def _approve_task(api_client: TestClient, board: dict, task_id: int) -> None:
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text


def _claim(api_client: TestClient, task_id: int, token: str) -> int:
    resp = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["participant"]["id"]


def _upload_to_task(
    api_client: TestClient,
    task_id: int,
    token: str,
    *,
    filename: str = "讲义.pdf",
    content: bytes = b"%PDF-1.4 fake subject material",
    content_type: str = "application/pdf",
) -> dict:
    resp = api_client.post(
        f"/tasks/{task_id}/attachments",
        headers=_auth(token),
        files={"file": (filename, io.BytesIO(content), content_type)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["attachment"]


def _upload_loose(
    api_client: TestClient,
    token: str,
    *,
    filename: str = "draft.pdf",
    content: bytes = b"%PDF-1.4 uploaded before the task existed",
) -> int:
    """走通用上传端点拿到一个还没挂到任何题上的文件 id。"""
    resp = api_client.post(
        "/attachments",
        headers=_auth(token),
        files={"file": (filename, io.BytesIO(content), "application/pdf")},
        data={"type": "file"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _list(api_client: TestClient, task_id: int, token: str | None) -> tuple[int, dict]:
    resp = api_client.get(
        f"/tasks/{task_id}/attachments",
        headers=_auth(token) if token else {},
    )
    return resp.status_code, resp.json() if resp.content else {}


def _download(api_client: TestClient, task_id: int, attachment_id: int, token: str):
    return api_client.get(
        f"/tasks/{task_id}/attachments/{attachment_id}/download",
        headers=_auth(token),
    )


# --- 挂得上、看得见、拿得到 ---------------------------------------------------


def test_the_publisher_attaches_a_file_and_the_list_says_who_may_take_it(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    """出题人传上去之后：他拿得到，板上的其他人看得见清单但拿不到文件。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="带材料的题")
    _, member_token = _member_of(user_client, api_client, board)

    attached = _upload_to_task(api_client, task_id, board["creator_token"])
    assert attached["name"] == "讲义.pdf"
    assert attached["size"] == len(b"%PDF-1.4 fake subject material")
    assert attached["contentType"] == "application/pdf"
    assert attached["downloadCount"] == 0
    # 存储给的是直链，发出来就等于绕过了下载那道门。
    assert "url" not in attached

    status, body = _list(api_client, task_id, board["creator_token"])
    assert status == 200
    assert [a["id"] for a in body["data"]["attachments"]] == [attached["id"]]
    assert body["data"]["canDownload"] is True

    status, body = _list(api_client, task_id, member_token)
    assert status == 200
    assert [a["id"] for a in body["data"]["attachments"]] == [attached["id"]]
    assert body["data"]["canDownload"] is False


def test_a_claimant_may_download_and_the_count_moves(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="领了才拿得到材料的题")
    _approve_task(api_client, board, task_id)
    _, member_token = _member_of(user_client, api_client, board)

    attached = _upload_to_task(api_client, task_id, board["creator_token"])
    assert (
        _download(api_client, task_id, attached["id"], member_token).status_code == 403
    )

    _claim(api_client, task_id, member_token)

    resp = _download(api_client, task_id, attached["id"], member_token)
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.4 fake subject material"
    assert resp.headers["content-type"].startswith("application/pdf")

    status, body = _list(api_client, task_id, member_token)
    assert status == 200
    assert body["data"]["canDownload"] is True
    assert body["data"]["attachments"][0]["downloadCount"] == 1


def test_a_board_manager_may_download_without_claiming_anything(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    """管理员是替出题人管这块板的人，不该为了看一眼材料先把自己报名上去。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="管理员要看材料的题")

    manager = user_client.create_user()
    manager_token = _login(user_client, api_client, manager)
    resp = api_client.post(
        f"/spaces/{board['space_id']}/managers",
        json={"userId": manager.user_id, "role": "ADMIN"},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 201, resp.text

    attached = _upload_to_task(api_client, task_id, board["creator_token"])
    assert (
        _download(api_client, task_id, attached["id"], manager_token).status_code == 200
    )


def test_someone_who_neither_publishes_nor_claims_may_not_download(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="没领就别拿走材料")
    _approve_task(api_client, board, task_id)
    _, member_token = _member_of(user_client, api_client, board)

    attached = _upload_to_task(api_client, task_id, board["creator_token"])
    resp = _download(api_client, task_id, attached["id"], member_token)
    assert resp.status_code == 403
    assert resp.content != b"%PDF-1.4 fake subject material"


def test_only_the_publisher_or_a_manager_may_put_a_file_on_the_task(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="别人不能往上面加材料")
    _, member_token = _member_of(user_client, api_client, board)

    resp = api_client.post(
        f"/tasks/{task_id}/attachments",
        headers=_auth(member_token),
        files={"file": ("替别人加的.pdf", io.BytesIO(b"nope"), "application/pdf")},
    )
    assert resp.status_code == 403, resp.text


def test_a_task_the_reader_cannot_see_does_not_give_up_its_material(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    """题本身设了可见范围，板上但不在范围里的人连清单都拿不到 —— 清单不比题更公开。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(
        api_client, board, name="限可见范围的题", access_control_enabled=True
    )
    _, member_token = _member_of(user_client, api_client, board)
    attached = _upload_to_task(api_client, task_id, board["creator_token"])

    status, _ = _list(api_client, task_id, member_token)
    assert status == 403
    assert (
        _download(api_client, task_id, attached["id"], member_token).status_code == 403
    )

    # 出题人不受影响：这是他自己的题。
    status, body = _list(api_client, task_id, board["creator_token"])
    assert status == 200
    assert body["data"]["canDownload"] is True


# --- 猜 id 的两条后果 ---------------------------------------------------------


def test_another_persons_file_cannot_be_attached_to_my_task(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    """附件 id 是连续整数。不校验的话，任何登录用户都能把别人交作业时附的材料
    挂到自己的题上，借这块板把它公开出去。"""
    board = _new_board(user_client, api_client)
    _, member_token = _member_of(user_client, api_client, board)

    mine = _upload_loose(api_client, board["creator_token"])
    theirs = _upload_loose(api_client, member_token, content="别人的作业材料".encode())

    resp = _post_task(api_client, board, name="偷材料的题", attachment_ids=[theirs])
    assert resp.status_code == 403, resp.text

    # 不存在的 id 是 400 而不是 500：这不是「找不到东西」，是请求本身不成立。
    resp = _post_task(
        api_client,
        board,
        name="挂一个不存在的 id",
        attachment_ids=[unique_int(900000000, 999999999)],
    )
    assert resp.status_code == 400, resp.text

    # 自己传的、还没挂过的，就能挂上。
    task_id = _create_task(
        api_client, board, name="带上自己的材料", attachment_ids=[mine]
    )
    status, body = _list(api_client, task_id, board["creator_token"])
    assert status == 200
    assert [a["id"] for a in body["data"]["attachments"]] == [mine]

    # 同一个文件挂第二次：一行文件不该同时属于两道题。
    resp = _post_task(api_client, board, name="重复引用", attachment_ids=[mine])
    assert resp.status_code == 400, resp.text


def test_a_file_on_another_task_is_not_reachable_by_swapping_the_task_id(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    board = _new_board(user_client, api_client)
    with_material = _create_task(api_client, board, name="材料在这道题上")
    empty = _create_task(api_client, board, name="这道题上没有材料")
    attached = _upload_to_task(api_client, with_material, board["creator_token"])

    resp = _download(api_client, empty, attached["id"], board["creator_token"])
    assert resp.status_code == 404, resp.text


# --- 摘下来 -------------------------------------------------------------------


def test_removing_takes_it_off_the_list_and_the_download_stops_answering(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="摘掉材料")
    attached = _upload_to_task(api_client, task_id, board["creator_token"])

    resp = api_client.delete(
        f"/tasks/{task_id}/attachments/{attached['id']}",
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 204, resp.text

    status, body = _list(api_client, task_id, board["creator_token"])
    assert status == 200
    assert body["data"]["attachments"] == []
    assert (
        _download(
            api_client, task_id, attached["id"], board["creator_token"]
        ).status_code
        == 404
    )


def test_only_the_publisher_or_a_manager_may_take_a_file_off(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="别人摘不掉")
    _, member_token = _member_of(user_client, api_client, board)
    attached = _upload_to_task(api_client, task_id, board["creator_token"])

    resp = api_client.delete(
        f"/tasks/{task_id}/attachments/{attached['id']}", headers=_auth(member_token)
    )
    assert resp.status_code == 403, resp.text

    status, body = _list(api_client, task_id, board["creator_token"])
    assert status == 200
    assert len(body["data"]["attachments"]) == 1


# --- 中文文件名 ---------------------------------------------------------------


def test_a_chinese_filename_downloads_instead_of_crashing(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    """``Content-Disposition`` 是 latin-1 的。把中文名直接写进 header，Starlette
    编码时报错，下载一份中文名的材料变成 500 —— 所以这里断的是「拿得到」。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="中文名的材料")
    attached = _upload_to_task(
        api_client,
        task_id,
        board["creator_token"],
        filename="第三讲 讲义（含附录）.pdf",
    )

    resp = _download(api_client, task_id, attached["id"], board["creator_token"])
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.4 fake subject material"
    disposition = resp.headers["content-disposition"]
    assert "attachment" in disposition
    assert "%E7%AC%AC%E4%B8%89%E8%AE%B2" in disposition  # 「第三讲」的 UTF-8 百分号编码
