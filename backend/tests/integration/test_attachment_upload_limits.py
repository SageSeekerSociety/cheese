"""单份附件的上限：**报出去的那个数，就是拦下来用的那个数**。

上限只有一个来源 —— ``settings.attachment_max_bytes``。两处读它：

- ``AttachmentService.upload``：字节进表之前判一次，多一个字节就 422（通用上传
  ``POST /attachments``、题目材料 ``POST /tasks/{id}/attachments``、PDF 发布都从
  这里过，所以一条判据把两条路由都覆盖了）；
- ``GET /attachments/limits``：上传之前把同一个数告诉前端（发题页那张附件卡上写的
  就是它）。

所以这一份按**「把配置改小，两边一起跟着动」**来量，而不是把 100MB 抄进断言里：报
的数换了、收下的字节数也在同一个位置换了，说明报的不是一份另写的字面量。上限压在
几 KB 上跑，是因为「超过」这件事与上限多大无关，而 100MB 的请求在测试里没有第二种
下场 —— 这里量的是那条界线在哪，不是那个数是多少。
"""

from __future__ import annotations

import io
import time

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

# 比默认值小得多，好把「正好等于上限」与「多一个字节」都真发一遍。
CEILING = 4096


@pytest.fixture
def upload_root(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """把本地存储落到 tmp_path（与 ``test_task_attachments.py`` 同一只 fixture：
    ``get_storage_backend()`` 是模块级单例，第一次调用就把 base_path 定死）。"""
    monkeypatch.setattr(settings, "storage_local_path", str(tmp_path))
    storage_module._storage_backend = None
    yield tmp_path
    storage_module._storage_backend = None


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _signed_in(
    user_client: UserCreator, api_client: TestClient
) -> tuple[CreatedUser, str]:
    user = user_client.create_user()
    return user, user_client.login(api_client, user.username, user.password)


def _loose_upload(
    client: TestClient, token: str, *, size: int, name: str = "material.bin"
):
    """走通用上传端点：文件先落到服务端，拿回 id 再挂到题上。"""
    return client.post(
        "/attachments",
        headers=_auth(token),
        files={"file": (name, io.BytesIO(b"x" * size), "application/octet-stream")},
        data={"type": "file"},
    )


def _task_upload(client: TestClient, task_id: int, token: str, *, size: int, name: str):
    """直接把一份材料挂到题上。"""
    return client.post(
        f"/tasks/{task_id}/attachments",
        headers=_auth(token),
        files={"file": (name, io.BytesIO(b"x" * size), "application/octet-stream")},
    )


def _board(user_client: UserCreator, api_client: TestClient) -> dict:
    creator = user_client.create_user()
    creator_token = user_client.login(api_client, creator.username, creator.password)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Attachment Limits ({suffix})",
            "intro": "一个题目板",
            "description": "放题目和材料的地方",
            "avatarId": 1,
            "taskTemplates": [],
        },
        headers=_auth(creator_token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]
    return {
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
            "accessControlEnabled": False,
        },
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["task"]["id"]


def test_the_reported_ceiling_is_the_one_that_refuses_the_upload(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root,
    monkeypatch: pytest.MonkeyPatch,
):
    """报的数 == 执行的那个配置：它一变，两边一起变。"""
    _, token = _signed_in(user_client, api_client)

    reported = api_client.get("/attachments/limits", headers=_auth(token))
    assert reported.status_code == 200
    body = reported.json()
    assert set(body.keys()) == {"code", "message", "data"}
    # 报的就是配置里那一个值（默认那个数是部署在用的那个，见 config.py 上那一段）。
    assert body["data"]["maxFileBytes"] == settings.attachment_max_bytes

    # 把配置改小：如果路由里写的是自己的一份字面量，这一条就会红。
    monkeypatch.setattr(settings, "attachment_max_bytes", CEILING)
    reported = api_client.get("/attachments/limits", headers=_auth(token))
    assert reported.json()["data"]["maxFileBytes"] == CEILING

    # 而那条界线真的在那个位置：正好等于上限的收下，多一个字节的拒掉。
    fits = _loose_upload(api_client, token, size=CEILING)
    assert fits.status_code == 201, fits.text
    assert fits.json()["data"]["id"] > 0

    over = _loose_upload(api_client, token, size=CEILING + 1)
    assert over.status_code == 422, over.text


def test_a_task_material_over_the_ceiling_is_refused_and_leaves_nothing_behind(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root,
    monkeypatch: pytest.MonkeyPatch,
):
    """题目材料那条路由同一个上限：拒了就是拒了，题上不会多出半份材料。"""
    board = _board(user_client, api_client)
    task_id = _create_task(api_client, board, name="带材料的题")
    token = board["creator_token"]
    monkeypatch.setattr(settings, "attachment_max_bytes", CEILING)

    fits = _task_upload(api_client, task_id, token, size=CEILING, name="收下的.bin")
    assert fits.status_code == 201, fits.text

    over = _task_upload(api_client, task_id, token, size=CEILING + 1, name="太大的.bin")
    assert over.status_code == 422, over.text

    listed = api_client.get(f"/tasks/{task_id}/attachments", headers=_auth(token))
    assert listed.status_code == 200, listed.text
    assert [a["name"] for a in listed.json()["data"]["attachments"]] == ["收下的.bin"]


def test_the_ceiling_is_behind_the_same_door_as_the_upload(api_client: TestClient):
    """与上传同一道门：登录了才问得到，没登录连数都拿不到。"""
    assert api_client.get("/attachments/limits").status_code == 401
