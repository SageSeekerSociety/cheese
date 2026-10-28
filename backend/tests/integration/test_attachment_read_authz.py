"""通用附件读路由：`GET /attachments/{id}` 与 `/download` 对谁给字节。

`#1786` 把「谁能下载一份题目材料」立成三条主张：出题人 / 板管理员 / 已经领取的人。
它们写在题目材料那条路上（`GET /tasks/{taskId}/files/raw`），材料在题目资料库里。
发题时选的文件先传成这里的一份附件、建题时拷进题目资料库，所以同一份字节在这里还
有一份 —— 这扇通用的大门曾经只要登录就答，**知道一个可枚举的小整数 id，就拿到了别人
题目材料的原始字节。**

判据有据可查（`app/domain/attachment/models.py` 里 `Attachment` 只有
`id / type / url / meta`，**没有**任何归属列）：图片对登录用户放行，其余只有上传者
本人能读。理由是另一条前端契约：公告与讨论里嵌的图（`AttachmentImage` 节点带的是 `attachmentId`）
   由**所有能看见那段内容的人**通过这条通用路由解析出来（`useAttachmentImages.ts`、
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
    """把本地存储落到 tmp_path（与 `test_task_files.py` 同一手）。"""
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


def _create_task(
    api_client: TestClient,
    board: dict,
    *,
    name: str,
    attachment_ids: list[int] | None = None,
) -> int:
    resp = api_client.post(
        "/tasks",
        json={
            "attachmentIds": attachment_ids or [],
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


def _claim(api_client: TestClient, task_id: int, token: str) -> int:
    resp = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    return int(resp.json()["data"]["participant"]["id"])


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


# --- 题目的材料：通用路由不替题目那道判据开门 ---------------------------------


def test_a_file_picked_for_a_task_is_not_readable_through_the_generic_routes(
    api_client: TestClient, user_client: UserCreator, upload_root: Path, board: dict
):
    """审计那条路径：出题人发题时选的文件，无关的人仅登录就去通用大门取字节。领了
    题的人也一样拿不到这一份：他读的是题目资料库里那一份（`/tasks/{id}/files`）。"""
    picked = _upload_loose(api_client, board["creator_token"], filename="payroll.csv")
    task_id = _create_task(
        api_client, board, name="材料在这道题上", attachment_ids=[picked]
    )
    _, member_token = _member_of(user_client, api_client, board)
    _claim(api_client, task_id, member_token)

    resp = _download(api_client, picked, member_token)
    assert resp.status_code == 403, resp.text
    assert SECRET_BYTES not in resp.content
    resp = _detail(api_client, picked, member_token)
    assert resp.status_code == 403, resp.text
    assert "storageKey" not in resp.text

    assert _download(api_client, picked, board["creator_token"]).status_code == 200


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
    看见那段内容的人都靠这条通用路由把它解析成 url（`useAttachmentImages.ts`、
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
