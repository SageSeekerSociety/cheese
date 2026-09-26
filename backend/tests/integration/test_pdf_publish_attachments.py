"""从一份 PDF 发出来的题：勾中的原 PDF 与抽出的插图，真的挂在每一道题上。

这条路与 ``POST /tasks`` 那条（``test_task_attachments.py``）不同的一点，也是这里
要钉住的全部内容：**同一份文件跟着每一道生成出来的题走**。一份 PDF 解析出三道题、
人勾了「原 PDF 与那几张插图」，三道题就都该带着它 —— 一份材料属于一道题那条规矩
在这条路上不成立，因为材料是这一批题共同的出处。

一件件钉的是浏览器与客户端真的收到什么：

1. 预览把两样东西**落成行**并报回 id（原 PDF 一份、抽出的插图各一张），行上的
   ``uploaderId`` 是**调用者自己** —— 服务端替人建的行要署名，否则确认那一步会被
   「只能挂自己上传的文件」挡回来；
2. 确认发布时勾中的 id 挂到**每一道**题上，且下载权限与第 4 批同一套口径：出题人 /
   板管理员 / 已经领取的人拿得到，其余人 403；
3. 不勾任何附件时，行为与这条路今天的样子完全一样（一道题都不带材料）。

解析那一步（PDF → 每页 Markdown + 插图）**不 stub**：这里真的造一份带图的 PDF、
真的让 ``pymupdf4llm`` 把图抽出来写成 PNG。只有大模型那一步被换掉（这套环境里没有
推理后端），换的是一个把提示词里那个图片标记原样放回 ``description`` 的假客户端 ——
所以「哪张图进了题干」这件事仍然是真的从 PDF 里读出来的。
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import fitz
import pytest
from fastapi.testclient import TestClient

from app.core import storage as storage_module
from app.core.config import settings
from app.domain.llm.llm_client import LLMResponse
from app.domain.task.task_pdf_draft_service import TaskPdfDraftService
from tests.integration.conftest import (
    CreatedUser,
    UserCreator,
    create_approved_space,
    unique_int,
)


@pytest.fixture
def upload_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把本地存储落到 tmp_path（与 ``test_task_attachments.py`` 同一段理由）。

    ``get_storage_backend()`` 是模块级单例，第一次调用就把 base_path 定死 —— 所以
    光改 settings 不够，还得先把已经建好的那个丢掉。收尾也丢掉一次：留在那里的话，
    下一个测试拿到的是指向已经删掉的 tmp_path 的 backend。
    """
    monkeypatch.setattr(settings, "storage_local_path", str(tmp_path))
    storage_module._storage_backend = None
    yield tmp_path
    storage_module._storage_backend = None


class _EchoingLLMClient:
    """把提示词里那个图片标记放回 ``description`` 的假客户端。

    没有推理后端可用，这一层必须换掉；但换掉的只是「读出文字」这件事 —— 那段提示词
    本身就是 ``pymupdf4llm`` 从真 PDF 里读出来的，图片标记也在里面，所以题干里最后
    留下的那张图，出处仍是 PDF 自己。
    """

    def __init__(self) -> None:
        self.is_configured = True
        self.prompts: list[str] = []

    async def get_completion(self, **kwargs) -> LLMResponse:
        prompt = str(kwargs.get("prompt") or "")
        self.prompts.append(prompt)
        # **最后一个**标记才是 PDF 里那一张：提示词开头举例用的那个 ``![描述](…)``
        # 是模板文字，不是这一页的图。
        markers = re.findall(r"!\[[^\]]*\]\([^)]*\)", prompt)
        marker = markers[-1] if markers else ""
        body = f"给定一段会崩的程序，说明它为什么崩。\n\n{marker}"
        return LLMResponse(
            content=(
                '{"name":"用 gdb 定位一次段错误","intro":"找出崩在哪一行。",'
                f'"description":{_json_string(body)}}}'
            ),
            total_tokens=321,
            prompt_tokens=200,
            completion_tokens=121,
        )


def _json_string(value: str) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def _one_page_pdf() -> bytes:
    """一页纸：一段文字 + 一张真的位图。pymupdf4llm 会把这张图写成 PNG。"""
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), 0)
    pixmap.set_rect(pixmap.irect, (255, 0, 0))

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Question 1: explain the segfault.", fontsize=12)
    page.insert_image(fitz.Rect(72, 140, 200, 268), stream=pixmap.tobytes("png"))
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@pytest.fixture
def llm(api_client: TestClient) -> Iterator[_EchoingLLMClient]:
    """这一份用例走的那张草稿服务：真解析，假推理。"""
    from app.api.routes.tasks import get_task_pdf_draft_service

    client = _EchoingLLMClient()
    api_client.app.dependency_overrides[get_task_pdf_draft_service] = lambda: (
        TaskPdfDraftService(llm_client=client)
    )
    yield client
    api_client.app.dependency_overrides.pop(get_task_pdf_draft_service, None)


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
            "name": f"PDF 发布附件 ({suffix})",
            "intro": "一块题板",
            "description": "从 PDF 发题的地方",
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
    """在这块板上、但既不是管理员也不是出题人的人。"""
    user = user_client.create_user()
    token = _login(user_client, api_client, user)
    resp = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": user.user_id},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 201, resp.text
    return user, token


def _preview(
    api_client: TestClient,
    board: dict,
    *,
    token: str | None = None,
    filename: str = "计算机系统基础-第五次作业.pdf",
):
    return api_client.post(
        "/tasks/publish/from-pdf/preview",
        headers=_auth(token or board["creator_token"]),
        data={"spaceId": str(board["space_id"]), "maxTasks": "5"},
        files={"file": (filename, io.BytesIO(_one_page_pdf()), "application/pdf")},
    )


def _confirm(
    api_client: TestClient,
    board: dict,
    *,
    drafts: list[dict[str, Any]],
    attachment_ids: list[int] | None = None,
    token: str | None = None,
):
    task_options: dict[str, Any] = {
        "name": "占位",
        "intro": "占位",
        "description": "占位",
        "space": board["space_id"],
        "categoryId": board["category_id"],
        "submitterType": "USER",
        "resubmittable": True,
        "editable": True,
        "defaultDeadline": 30,
        "deadline": None,
    }
    if attachment_ids is not None:
        task_options["attachmentIds"] = attachment_ids
    return api_client.post(
        "/tasks/publish/from-pdf/confirm",
        headers=_auth(token or board["creator_token"]),
        json={"drafts": drafts, "taskOptions": task_options},
    )


def _previewed_drafts(preview) -> list[dict[str, Any]]:
    """把预览回来的草稿整理成确认发布要的那一份（``PdfTaskDraftData`` 那几个字段）。"""
    return [
        {
            "name": draft["name"],
            "intro": draft["intro"],
            "description": draft["description"],
            "space": draft["space"],
            "categoryId": draft["categoryId"],
        }
        for draft in preview.json()["data"]["drafts"]
    ]


def _list(api_client: TestClient, task_id: int, token: str):
    resp = api_client.get(f"/tasks/{task_id}/attachments", headers=_auth(token))
    return resp.status_code, resp.json() if resp.status_code == 200 else None


def _download(api_client: TestClient, task_id: int, attachment_id: int, token: str):
    return api_client.get(
        f"/tasks/{task_id}/attachments/{attachment_id}/download",
        headers=_auth(token),
    )


def _approve(api_client: TestClient, board: dict, task_id: int) -> None:
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text


def _claim(api_client: TestClient, task_id: int, token: str) -> None:
    resp = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text


# --- 预览：两样东西落成行，id 报回来 -------------------------------------------


def test_preview_registers_the_pdf_and_each_illustration_under_the_caller(
    api_client: TestClient, user_client: UserCreator, upload_root: Path, llm
):
    board = _new_board(user_client, api_client)

    resp = _preview(api_client, board)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    # 草稿里那张图是真的从 PDF 里抽出来的（提示词里带的标记被放回 description）。
    assert "![" in data["drafts"][0]["description"]

    attachments = data["attachments"]
    pdf = attachments["pdf"]
    assert pdf["id"] > 0
    assert pdf["name"] == "计算机系统基础-第五次作业.pdf"
    assert pdf["contentType"] == "application/pdf"
    assert pdf["size"] > 0

    images = attachments["images"]
    assert len(images) == 1, images
    assert images[0]["id"] != pdf["id"]
    assert images[0]["contentType"] == "image/png"
    assert images[0]["name"].endswith(".png")
    assert images[0]["size"] > 0

    # 报给前端的**没有 url**：存储给的是直链，发出去就等于绕开下载那道门。
    for item in [pdf, *images]:
        assert "url" not in item

    # 行署名给调用者：服务端自己建的行如果没署名，确认那一步会被「只能挂自己上传的
    # 文件」挡回来 —— 所以这里真的把这两样发一遍，再回读挂在题上那一行的 uploaderId。
    confirmed = _confirm(
        api_client,
        board,
        drafts=_previewed_drafts(resp),
        attachment_ids=[pdf["id"], images[0]["id"]],
    )
    assert confirmed.status_code == 200, confirmed.text
    task_id = confirmed.json()["data"]["tasks"][0]["id"]

    status, body = _list(api_client, task_id, board["creator_token"])
    assert status == 200
    assert {a["uploaderId"] for a in body["data"]["attachments"]} == {
        board["creator"].user_id
    }


# --- 确认：勾中的文件挂在每一道题上 -------------------------------------------


def test_the_checked_files_ride_along_with_every_task_of_the_batch(
    api_client: TestClient, user_client: UserCreator, upload_root: Path, llm
):
    board = _new_board(user_client, api_client)

    preview = _preview(api_client, board)
    assert preview.status_code == 200, preview.text
    data = preview.json()["data"]
    checked = [data["attachments"]["pdf"]["id"], data["attachments"]["images"][0]["id"]]

    # 一条草稿复制成三条：一份 PDF 解出多道题是这条路存在的理由。
    drafts = _previewed_drafts(preview) * 3
    for index, draft in enumerate(drafts):
        draft["name"] = f"{draft['name']}（第 {index + 1} 道）"

    resp = _confirm(api_client, board, drafts=drafts, attachment_ids=checked)
    assert resp.status_code == 200, resp.text
    tasks = resp.json()["data"]["tasks"]
    assert len(tasks) == 3

    for task in tasks:
        status, body = _list(api_client, task["id"], board["creator_token"])
        assert status == 200
        assert {a["id"] for a in body["data"]["attachments"]} == set(checked)
        # 出题人拿得到文件。
        assert body["data"]["canDownload"] is True
        pdf_row = next(a for a in body["data"]["attachments"] if a["id"] == checked[0])
        assert pdf_row["name"] == "计算机系统基础-第五次作业.pdf"

        # 而且**真的下得动**：清单里有它，字节就取得到。
        download = _download(api_client, task["id"], checked[0], board["creator_token"])
        assert download.status_code == 200, download.text
        assert download.content.startswith(b"%PDF-")


def test_nothing_checked_leaves_the_tasks_bare(
    api_client: TestClient, user_client: UserCreator, upload_root: Path, llm
):
    """不勾附件时与今天完全一样 —— 不许顺手让所有 PDF 题都带上材料。"""
    board = _new_board(user_client, api_client)
    preview = _preview(api_client, board)
    assert preview.status_code == 200, preview.text

    resp = _confirm(
        api_client, board, drafts=_previewed_drafts(preview), attachment_ids=None
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["tasks"][0]["id"]

    status, body = _list(api_client, task_id, board["creator_token"])
    assert status == 200
    assert body["data"]["attachments"] == []


# --- 下载权限：与第 4 批同一套口径 -------------------------------------------


def test_a_member_downloads_only_after_claiming_the_task(
    api_client: TestClient, user_client: UserCreator, upload_root: Path, llm
):
    board = _new_board(user_client, api_client)
    _, member_token = _member_of(user_client, api_client, board)

    preview = _preview(api_client, board)
    assert preview.status_code == 200, preview.text
    data = preview.json()["data"]
    pdf_id = data["attachments"]["pdf"]["id"]
    image_id = data["attachments"]["images"][0]["id"]

    resp = _confirm(
        api_client,
        board,
        drafts=_previewed_drafts(preview),
        attachment_ids=[pdf_id, image_id],
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["tasks"][0]["id"]
    _approve(api_client, board, task_id)

    # 还没领：清单看得见（看得见这道题就看得见材料），文件拿不到。
    status, body = _list(api_client, task_id, member_token)
    assert status == 200, body
    assert {a["id"] for a in body["data"]["attachments"]} == {pdf_id, image_id}
    assert body["data"]["canDownload"] is False
    assert _download(api_client, task_id, pdf_id, member_token).status_code == 403
    assert _download(api_client, task_id, image_id, member_token).status_code == 403

    # 领了之后拿得到。
    _claim(api_client, task_id, member_token)
    status, body = _list(api_client, task_id, member_token)
    assert status == 200
    assert body["data"]["canDownload"] is True
    download = _download(api_client, task_id, pdf_id, member_token)
    assert download.status_code == 200, download.text
    # 插图也是同一份口径：那是同一批材料，不该一半给一半不给。
    assert _download(api_client, task_id, image_id, member_token).status_code == 200


# --- 猜 id 的后果 -------------------------------------------------------------


def test_someone_elses_previewed_files_cannot_be_published_under_my_name(
    api_client: TestClient, user_client: UserCreator, upload_root: Path, llm
):
    """文件行是服务端替**调用者**建的 —— 换个人拿着那两个 id 发题，就该被挡住。

    这一条同时钉住了「行上的 uploaderId 是谁」：预览报回来的 id 是可猜的连续整数，
    没有这道校验，板上任何人都能把别人解析出来的 PDF 挂到自己的题上。
    """
    board = _new_board(user_client, api_client)
    _, member_token = _member_of(user_client, api_client, board)

    preview = _preview(api_client, board)
    assert preview.status_code == 200, preview.text
    data = preview.json()["data"]
    stolen = [data["attachments"]["pdf"]["id"], data["attachments"]["images"][0]["id"]]

    resp = _confirm(
        api_client,
        board,
        drafts=_previewed_drafts(preview),
        attachment_ids=stolen,
        token=member_token,
    )
    assert resp.status_code == 403, resp.text

    # 一个不存在的 id 是 400：请求本身不成立，不是「找不到东西」。
    resp = _confirm(
        api_client,
        board,
        drafts=_previewed_drafts(preview),
        attachment_ids=[unique_int(900000000, 999999999)],
    )
    assert resp.status_code == 400, resp.text


def test_a_batch_that_fails_the_attachment_check_leaves_no_half_published_tasks(
    api_client: TestClient, user_client: UserCreator, upload_root: Path, llm
):
    """一道题都不该留下：要么整批带材料发出去，要么一道都没发。"""
    board = _new_board(user_client, api_client)
    preview = _preview(api_client, board)
    data = preview.json()["data"]
    good = data["attachments"]["pdf"]["id"]

    drafts = _previewed_drafts(preview)
    resp = _confirm(
        api_client,
        board,
        drafts=drafts,
        attachment_ids=[good, unique_int(900000000, 999999999)],
    )
    assert resp.status_code == 400, resp.text

    # 「我发布的」那一眼里没有它 —— 这一批整个回滚了（``get_db`` 异常时 rollback），
    # 不是「发了一半、材料没挂上」。
    listed = api_client.get(
        f"/spaces/{board['space_id']}/me/publishing/tasks",
        headers=_auth(board["creator_token"]),
    )
    assert listed.status_code == 200, listed.text
    assert "gdb" not in listed.text, listed.text
