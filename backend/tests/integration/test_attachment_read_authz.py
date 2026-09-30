"""通用附件读路由：`GET /attachments/{id}` 与 `/download` 对谁给字节。

`#1786` 把「谁能下载一份题目材料」立成三条主张，全部写在题内那条路上
（`GET /tasks/{taskId}/attachments/{attachmentId}/download`）：出题人 / 板管理员 /
已经领取的人。可同一个文件还有一扇通用的大门 —— `GET /attachments/{attachmentId}`
与它下面那条 `/download` 只要登录就答，函数体里一行访问判断都没有；同一个文件里的
`DELETE` 反倒是 owner-gated。三种动作两种口径，于是题内那道闸门可以整个绕过：
**知道一个可枚举的小整数 id，就拿到了别人材料库里的原始字节。**

判据定在两件事上，各自有据可查（`app/domain/attachment/models.py` 里 `Attachment`
只有 `id / type / url / meta`，**没有**任何归属列）：

1. **挂在某道题上的文件**：判据照抄题内那条路，一个字不另立 —— 出题人 / 板管理员 /
   已领取的人能读，其余登录用户 403。这是 `task_attachment` 这张关联表能回答的问题，
   也是审计点名的那个洞。
2. **不挂在任何题上的文件**：图片对登录用户放行，其余只有上传者本人能读。理由是另
   一条前端契约：公告与讨论里嵌的图（`AttachmentImage` 节点带的是 `attachmentId`）
   由**所有能看见那段内容的人**通过这条通用路由解析出来（`ImageView.vue`、
   `Discussions.vue`），把它们掐掉就是一片裂图。而 `type=file` 的散件（交作业的材料、
   建题前先传上去的文件）没有任何一处前端会跨用户用这条路由去读，所以收紧到上传者本人。

**这是缺口，不是完整答案**：`attachment` 上没有归属列，富文本里嵌的 id 也无法反查，
所以「一张散图对任何登录用户可读」这条本次**没有**修 —— 它被下面那条测试钉住，
换判据的人必须显式改掉它。
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

# 一份不该被无关的人拿到的字节。断言的是这几个字面量本身，不是「某个字段在不在」：
# 审计证明的也正是「原始字节到了别人手里」。
SECRET_BYTES = b"PRIVATE-PAYROLL-SPREADSHEET-BYTES"
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


@pytest.fixture
def upload_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把本地存储落到 tmp_path（与 `test_task_attachments.py` 同一手）。"""
    monkeypatch.setattr(settings, "storage_local_path", str(tmp_path))
    storage_module._storage_backend = None
    yield tmp_path
    storage_module._storage_backend = None


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(user_client: UserCreator, api_client: TestClient, user: CreatedUser) -> str:
    return user_client.login(api_client, user.username, user.password)


@pytest.fixture
def board(user_client: UserCreator, api_client: TestClient) -> dict:
    creator = user_client.create_user()
    creator_token = _login(user_client, api_client, creator)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Attachment Read Authz ({suffix})",
            "intro": "一块板",
            "description": "材料放在这道题上",
            "avatarId": 1,
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
    """板上的人 —— 看得见这块板，但不是管理员、不是出题人、也没领过题。"""
    user = user_client.create_user()
    token = _login(user_client, api_client, user)
    resp = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": user.user_id},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 201, resp.text
    return user, token


def _a_manager_of(
    user_client: UserCreator, api_client: TestClient, board: dict
) -> tuple[CreatedUser, str]:
    user = user_client.create_user()
    token = _login(user_client, api_client, user)
    resp = api_client.post(
        f"/spaces/{board['space_id']}/managers",
        json={"userId": user.user_id, "role": "ADMIN"},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 201, resp.text
    return user, token


def _claim(api_client: TestClient, task_id: int, token: str) -> int:
    resp = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    return int(resp.json()["data"]["participant"]["id"])


def _upload_to_task(
    api_client: TestClient,
    task_id: int,
    token: str,
    *,
    content: bytes = SECRET_BYTES,
    filename: str = "payroll.csv",
) -> int:
    resp = api_client.post(
        f"/tasks/{task_id}/attachments",
        headers=_auth(token),
        files={"file": (filename, io.BytesIO(content), "text/csv")},
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["data"]["attachment"]["id"])


def _upload_loose(
    api_client: TestClient,
    token: str,
    *,
    attachment_type: str = "file",
    content: bytes = SECRET_BYTES,
    filename: str = "loose.csv",
    content_type: str = "text/csv",
) -> int:
    """走通用上传端点拿一个还没挂到任何题上的文件 id。"""
    resp = api_client.post(
        "/attachments",
        headers=_auth(token),
        files={"file": (filename, io.BytesIO(content), content_type)},
        data={"type": attachment_type},
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["data"]["id"])


def _detail(api_client: TestClient, attachment_id: int, token: str | None):
    return api_client.get(
        f"/attachments/{attachment_id}", headers=_auth(token) if token else {}
    )


def _download(api_client: TestClient, attachment_id: int, token: str | None):
    return api_client.get(
        f"/attachments/{attachment_id}/download",
        headers=_auth(token) if token else {},
    )


# --- 题内那份材料：通用路由也得走题内那道判据 ---------------------------------


def test_a_task_material_is_not_readable_through_the_generic_routes(
    api_client: TestClient, user_client: UserCreator, upload_root: Path, board: dict
):
    """审计那条路径：材料挂在题上，无关的第三个人仅登录就去通用大门取字节。"""
    task_id = _create_task(api_client, board, name="材料在这道题上")
    attachment_id = _upload_to_task(api_client, task_id, board["creator_token"])
    _, outsider_token = _member_of(user_client, api_client, board)

    resp = _download(api_client, attachment_id, outsider_token)
    assert resp.status_code == 403, resp.text
    assert SECRET_BYTES not in resp.content

    resp = _detail(api_client, attachment_id, outsider_token)
    assert resp.status_code == 403, resp.text
    assert "storageKey" not in resp.text


def test_the_task_materials_readers_still_get_it_through_the_generic_routes(
    api_client: TestClient, user_client: UserCreator, upload_root: Path, board: dict
):
    """该放行的照旧放行：出题人、板管理员、已领取的人 —— 与题内那条路同一批人。"""
    task_id = _create_task(api_client, board, name="题的读者拿得到材料")
    attachment_id = _upload_to_task(api_client, task_id, board["creator_token"])

    resp = _download(api_client, attachment_id, board["creator_token"])
    assert resp.status_code == 200, resp.text
    assert resp.content == SECRET_BYTES

    _, manager_token = _a_manager_of(user_client, api_client, board)
    assert _download(api_client, attachment_id, manager_token).status_code == 200

    _, member_token = _member_of(user_client, api_client, board)
    assert _download(api_client, attachment_id, member_token).status_code == 403
    _claim(api_client, task_id, member_token)
    resp = _download(api_client, attachment_id, member_token)
    assert resp.status_code == 200, resp.text
    assert resp.content == SECRET_BYTES


# --- 没挂在任何题上的散件 -----------------------------------------------------


def test_an_unrelated_user_cannot_take_a_loose_file(
    api_client: TestClient, user_client: UserCreator, upload_root: Path, board: dict
):
    """散件（交作业的材料、建题前先传上去的文件）只有上传者本人能读。"""
    attachment_id = _upload_loose(api_client, board["creator_token"])
    _, outsider_token = _member_of(user_client, api_client, board)

    resp = _download(api_client, attachment_id, outsider_token)
    assert resp.status_code == 403, resp.text
    assert SECRET_BYTES not in resp.content

    resp = _detail(api_client, attachment_id, outsider_token)
    assert resp.status_code == 403, resp.text
    assert "storageKey" not in resp.text


def test_the_uploader_still_reads_their_own_loose_file(
    api_client: TestClient, user_client: UserCreator, upload_root: Path, board: dict
):
    attachment_id = _upload_loose(api_client, board["creator_token"])

    resp = _detail(api_client, attachment_id, board["creator_token"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["attachment"]["meta"]["filename"] == "loose.csv"

    resp = _download(api_client, attachment_id, board["creator_token"])
    assert resp.status_code == 200, resp.text
    assert resp.content == SECRET_BYTES


def test_an_image_embedded_in_a_document_is_still_readable_by_another_viewer(
    api_client: TestClient, user_client: UserCreator, upload_root: Path, board: dict
):
    """缺口被钉在这里，不让它悄悄变。

    公告与讨论里嵌的图就是一张散图：`AttachmentImage` 节点带 `attachmentId`，每个能
    看见那段内容的人都靠这条通用路由把它解析成 url（`ImageView.vue`、
    `Discussions.vue`）。收紧到「只有上传者本人」会让板上所有人看到的都是裂图。
    `attachment` 上没有归属列、富文本里的 id 也反查不到容器，所以这一侧只能维持现状
    —— 这是设计缺口，不是判据。
    """
    image_id = _upload_loose(
        api_client,
        board["creator_token"],
        attachment_type="image",
        content=PNG_1PX,
        filename="cover.png",
        content_type="image/png",
    )
    _, viewer_token = _member_of(user_client, api_client, board)

    resp = _detail(api_client, image_id, viewer_token)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["attachment"]["url"]


# --- 匿名 ---------------------------------------------------------------------


def test_anonymous_callers_are_rejected(
    api_client: TestClient, user_client: UserCreator, upload_root: Path, board: dict
):
    attachment_id = _upload_loose(api_client, board["creator_token"])
    assert _detail(api_client, attachment_id, None).status_code == 401
    assert _download(api_client, attachment_id, None).status_code == 401
