"""题目的材料与版本 (#4767 第一节)：谁放得进、谁看得见、谁拿得到，改一次记一版，
从题目来的项目在资料库的 `题目/` 下读到它们，只能读。

题目的材料不能比题目本身更保密，也不能比它更公开：

1. **放进 / 替换 / 删掉**：出题人本人，或这块板的所有者 / 管理员；
2. **看得见清单**：看得见这道题的人（题目详情那三道闸）；
3. **拿得到文件**：出题人 / 板管理员 / 申请通过、仍然有效的人。

发布是第 1 版；说明、提交内容、评分要点、材料每变一次记下一版，写明改了什么。
"""

from __future__ import annotations

import io
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import storage as storage_module
from app.core.config import settings
from app.domain.project.models import Project
from app.domain.task import package
from app.domain.task.models import TaskMembership
from tests.integration.conftest import (
    CreatedUser,
    UserCreator,
    create_approved_space,
    unique_int,
)
from tests.support import library_uploads

MATERIAL = b"%PDF-1.4 fake subject material"


@pytest.fixture
def upload_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """先传成附件的那几份落在 tmp_path（``get_storage_backend()`` 是模块级单例，
    第一次调用就定死了目录，所以先把已经建好的那个丢掉）。"""
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
            "name": f"Task Files ({suffix})",
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


def _post_task(api_client: TestClient, board: dict, *, name: str, **more):
    body: dict = {
        "name": name,
        "intro": "题",
        "description": "照着说明做",
        "space": board["space_id"],
        "categoryId": board["category_id"],
        "submitterType": "USER",
        "resubmittable": True,
        "editable": True,
        "defaultDeadline": 30,
        "deadline": int(time.time() * 1000) + 7 * 86400 * 1000,
        "submissionSchema": [{"prompt": "实验报告", "type": "TEXT"}],
        **more,
    }
    return api_client.post("/tasks", json=body, headers=_auth(board["creator_token"]))


def _create_task(
    api_client: TestClient, board: dict, *, name: str, approve: bool = True, **more
) -> int:
    resp = _post_task(api_client, board, name=name, **more)
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    if approve:
        _approve_task(api_client, board, task_id)
    return task_id


def _approve_task(api_client: TestClient, board: dict, task_id: int) -> None:
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text


def _claim(
    api_client: TestClient, board: dict, task_id: int, token: str, *, approve=True
) -> int:
    resp = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    participant = resp.json()["data"]["participant"]["id"]
    if approve:
        resp = api_client.patch(
            f"/tasks/{task_id}/participants/{participant}",
            json={"approved": "APPROVED"},
            headers=_auth(board["creator_token"]),
        )
        assert resp.status_code == 200, resp.text
    return participant


def _upload_loose(api_client: TestClient, token: str, name: str, data: bytes) -> int:
    """发题页那条路：先传成一份附件，拿到 id，建题时带上。"""
    resp = api_client.post(
        "/attachments",
        headers=_auth(token),
        files={"file": (name, io.BytesIO(data), "application/pdf")},
        data={"type": "file"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _put(api_client: TestClient, task_id: int, token: str, name: str, data: bytes):
    """领一次上传、把字节一段一段放上去，交回登记要带的凭据和哈希：材料那一页的上传。"""
    base = f"/tasks/{task_id}/files"
    slot = api_client.post(
        f"{base}/uploads",
        json={"name": name, "size": len(data)},
        headers=_auth(token),
    )
    if slot.status_code != 200:
        return slot, None
    refused, uploaded = library_uploads.send_parts(
        api_client, base, slot.json()["data"], data, _auth(token)
    )
    assert refused is None, refused.text
    return None, uploaded


def _add(api_client: TestClient, task_id: int, token: str, name: str, data: bytes):
    refused, uploaded = _put(api_client, task_id, token, name, data)
    if refused is not None:
        return refused
    return api_client.post(
        f"/tasks/{task_id}/files",
        json={**uploaded, "filename": name},
        headers=_auth(token),
    )


def _replace(api_client: TestClient, task_id: int, token: str, name: str, data: bytes):
    refused, uploaded = _put(api_client, task_id, token, name, data)
    if refused is not None:
        return refused
    return api_client.put(
        f"/tasks/{task_id}/files",
        params={"name": name},
        json=uploaded,
        headers=_auth(token),
    )


def _files(api_client: TestClient, task_id: int, token: str):
    return api_client.get(f"/tasks/{task_id}/files", headers=_auth(token))


def _download(api_client: TestClient, task_id: int, token: str, name: str):
    return api_client.get(
        f"/tasks/{task_id}/files/raw", params={"name": name}, headers=_auth(token)
    )


def _project_from(api_client: TestClient, task_id: int, token: str) -> str:
    """开始做这道题（已经开始了的就是继续做）：从题目来的项目只有这一条路。"""
    resp = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["project"]["id"]


# --- 放得进、看得见、拿得到 ------------------------------------------------------


def test_files_picked_on_the_publish_page_are_the_tasks_materials(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    """发题时先传成附件的那几份，发布之后就是这道题的材料，出题人拿得到原样的字节；
    清单里没有那份生成的题目说明。"""
    board = _new_board(user_client, api_client)
    picked = _upload_loose(api_client, board["creator_token"], "讲义.pdf", MATERIAL)
    task_id = _create_task(api_client, board, name="带材料的题", attachmentIds=[picked])

    resp = _files(api_client, task_id, board["creator_token"])
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert [(f["name"], f["size"]) for f in body["files"]] == [
        ("讲义.pdf", len(MATERIAL))
    ]
    assert body["canDownload"] is True
    resp = _download(api_client, task_id, board["creator_token"], "讲义.pdf")
    assert resp.status_code == 200
    assert resp.content == MATERIAL


def test_another_persons_upload_cannot_become_my_tasks_material(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    """附件 id 是连续整数：不认上传者的话，谁都能把别人交作业附的文件拷进自己的
    题目、借这块板公开出去。"""
    board = _new_board(user_client, api_client)
    _, member_token = _member_of(user_client, api_client, board)
    theirs = _upload_loose(api_client, member_token, "别人的作业.pdf", b"theirs")

    resp = _post_task(api_client, board, name="偷材料的题", attachmentIds=[theirs])
    assert resp.status_code == 403, resp.text


def test_the_list_is_for_who_sees_the_task_and_the_bytes_for_who_took_it(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="领了才拿得到材料的题")
    assert (
        _add(api_client, task_id, board["creator_token"], "数据.csv", b"a,b\n1,2\n")
    ).status_code == 201
    _, member_token = _member_of(user_client, api_client, board)

    resp = _files(api_client, task_id, member_token)
    assert resp.status_code == 200
    assert [f["name"] for f in resp.json()["data"]["files"]] == ["数据.csv"]
    assert resp.json()["data"]["canDownload"] is False
    assert _download(api_client, task_id, member_token, "数据.csv").status_code == 403

    _claim(api_client, board, task_id, member_token)
    assert resp.status_code == 200
    resp = _download(api_client, task_id, member_token, "数据.csv")
    assert resp.status_code == 200
    assert resp.content == b"a,b\n1,2\n"


def test_a_task_the_reader_cannot_see_does_not_list_its_materials(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(
        api_client, board, name="限可见范围的题", accessControlEnabled=True
    )
    _add(api_client, task_id, board["creator_token"], "答案提示.txt", b"secret")
    _, member_token = _member_of(user_client, api_client, board)

    # 对他来说这道题不在（题目详情给的也是这个回答）。
    assert _files(api_client, task_id, member_token).status_code == 404


def test_only_the_publisher_or_a_manager_changes_the_materials(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="别人不能动材料")
    _add(api_client, task_id, board["creator_token"], "讲义.pdf", MATERIAL)
    _, member_token = _member_of(user_client, api_client, board)
    _claim(api_client, board, task_id, member_token)

    assert (
        _add(api_client, task_id, member_token, "替别人加的.pdf", b"x").status_code
        == 403
    )
    resp = api_client.delete(
        f"/tasks/{task_id}/files",
        params={"name": "讲义.pdf"},
        headers=_auth(member_token),
    )
    assert resp.status_code == 403
    names = [
        f["name"]
        for f in _files(api_client, task_id, board["creator_token"]).json()["data"][
            "files"
        ]
    ]
    assert names == ["讲义.pdf"]


def test_the_generated_brief_cannot_be_replaced_or_removed_by_hand(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="说明是生成的")

    resp = _replace(
        api_client, task_id, board["creator_token"], package.BRIEF, b"# hand made"
    )
    assert resp.status_code == 422, resp.text
    resp = api_client.delete(
        f"/tasks/{task_id}/files",
        params={"name": package.BRIEF},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 422, resp.text


# --- 版本 ------------------------------------------------------------------------


def test_every_change_to_the_content_is_a_new_version_saying_what_changed(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="会改的题")
    token = board["creator_token"]
    _, member_token = _member_of(user_client, api_client, board)
    _claim(api_client, board, task_id, member_token)
    project_id = _project_from(api_client, task_id, member_token)

    def brief() -> str:
        resp = api_client.get(
            f"/projects/{project_id}/library/text",
            params={"path": f"题目/{package.BRIEF}"},
            headers=_auth(member_token),
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]["content"]

    assert "第 1 版" in brief()

    assert _add(api_client, task_id, token, "数据.csv", b"v1").status_code == 201
    assert _replace(api_client, task_id, token, "数据.csv", b"v2").status_code == 200
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={"description": "说明改了：多做一个对比实验"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    # 只改了截止时间：题目内容没变，不记版。
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={"deadline": int(time.time() * 1000) + 14 * 86400 * 1000},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text

    assert "第 4 版" in brief()
    assert "多做一个对比实验" in brief()
    progress = api_client.get(
        f"/projects/{project_id}/progress", headers=_auth(member_token)
    )
    assert progress.status_code == 200, progress.text
    revised = [
        (item["version"], item["changes"])
        for item in progress.json()["data"]["data"]
        if item["kind"] == "revised"
    ]
    assert revised == [
        (4, {"description": True}),
        (3, {"files": {"replaced": ["数据.csv"]}}),
        (2, {"files": {"added": ["数据.csv"]}}),
    ]

    # 交上去的那一份记着它是按第几版交的。
    participant = api_client.get(
        f"/tasks/{task_id}/participants", headers=_auth(token)
    ).json()["data"]["participants"][0]["id"]
    resp = api_client.post(
        f"/tasks/{task_id}/participants/{participant}/submissions",
        json=[{"text": "报告"}],
        headers=_auth(member_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["submission"]["packageRevision"] == 4


def test_a_rubric_kept_from_students_stays_out_of_the_brief(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(
        api_client,
        board,
        name="不公开评分要点的题",
        rubric=[{"item": "正确性", "points": 60, "criteria": "隐藏用例全过"}],
        rubricPublic=False,
    )
    resp = _download(api_client, task_id, board["creator_token"], package.BRIEF)
    assert resp.status_code == 200
    assert "隐藏用例全过" not in resp.content.decode()

    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={"rubricPublic": True},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text
    brief = _download(api_client, task_id, board["creator_token"], package.BRIEF)
    assert "第 2 版" in brief.content.decode()
    assert "隐藏用例全过" in brief.content.decode()


# --- 项目里的 `题目/` ----------------------------------------------------------------


def test_a_project_from_the_task_reads_its_materials_under_the_task_folder(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="项目里读材料")
    _add(api_client, task_id, board["creator_token"], "数据.csv", b"a,b\n")
    _, member_token = _member_of(user_client, api_client, board)
    _claim(api_client, board, task_id, member_token)
    project_id = _project_from(api_client, task_id, member_token)
    headers = _auth(member_token)

    top = api_client.get(f"/projects/{project_id}/library", headers=headers)
    assert top.status_code == 200, top.text
    [folder] = [row for row in top.json()["data"]["data"] if row["path"] == "题目"]
    assert folder["type"] == "folder"
    assert folder["readonly"] is True
    assert folder["count"] == 2

    inside = api_client.get(
        f"/projects/{project_id}/library", params={"dir": "题目"}, headers=headers
    ).json()["data"]["data"]
    assert sorted(row["path"] for row in inside) == [
        "题目/数据.csv",
        f"题目/{package.BRIEF}",
    ]
    assert all(row["readonly"] for row in inside)

    raw = api_client.get(
        f"/projects/{project_id}/library/raw",
        params={"path": "题目/数据.csv"},
        headers=headers,
    )
    assert raw.status_code == 200
    assert raw.content == b"a,b\n"

    found = api_client.get(
        f"/projects/{project_id}/library", params={"q": "数据"}, headers=headers
    ).json()["data"]["data"]
    assert [row["path"] for row in found] == ["题目/数据.csv"]

    # 出题人换了一份，项目里下一次读到的就是新的。
    _replace(api_client, task_id, board["creator_token"], "数据.csv", b"c,d\n")
    raw = api_client.get(
        f"/projects/{project_id}/library/raw",
        params={"path": "题目/数据.csv"},
        headers=headers,
    )
    assert raw.content == b"c,d\n"


def test_nothing_in_the_project_writes_under_the_task_folder(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="题目文件夹只读")
    _add(api_client, task_id, board["creator_token"], "数据.csv", b"a,b\n")
    _, member_token = _member_of(user_client, api_client, board)
    _claim(api_client, board, task_id, member_token)
    project_id = _project_from(api_client, task_id, member_token)
    headers = _auth(member_token)
    base = f"/projects/{project_id}/library"

    def put(name: str, folder: str | None = None):
        return library_uploads.put_into(
            api_client, base, name, b"abc", folder=folder, headers=headers
        )

    assert put("我的.csv", folder="题目").status_code == 422
    assert (
        api_client.delete(
            base, params={"path": "题目/数据.csv"}, headers=headers
        ).status_code
        == 422
    )
    assert (
        api_client.post(
            f"{base}/move",
            json={"path": "题目/数据.csv", "to": "数据.csv"},
            headers=headers,
        ).status_code
        == 422
    )
    assert put("笔记.md").status_code == 200
    assert (
        api_client.post(
            f"{base}/move",
            json={"path": "笔记.md", "to": "题目/笔记.md"},
            headers=headers,
        ).status_code
        == 422
    )
    inside = api_client.get(base, params={"dir": "题目"}, headers=headers).json()[
        "data"
    ]["data"]
    assert sorted(row["path"] for row in inside) == [
        "题目/数据.csv",
        f"题目/{package.BRIEF}",
    ]


def test_the_projects_agents_are_told_the_version_and_what_changed(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,
    db_session,
    _portal,
):
    """项目里的 agent 每一轮读到的「题目」那一段：第几版、材料在哪、这一版改了什么。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="agent 要知道题目变了")
    _, member_token = _member_of(user_client, api_client, board)
    _claim(api_client, board, task_id, member_token)
    project_id = _project_from(api_client, task_id, member_token)

    def section() -> str:
        async def read() -> str | None:
            project = await db_session.get(Project, uuid.UUID(project_id))
            return await package.section_for_project(db_session, project)

        found = _portal.call(read)
        assert found is not None
        return found

    first = section()
    assert "第 1 版" in first
    assert "题目/" in first

    _add(api_client, task_id, board["creator_token"], "数据.csv", b"a,b\n")
    second = section()
    assert "第 2 版" in second
    assert "新增文件 数据.csv" in second
    # 和项目总览上那一行一样，指到历史版本页上的这一版。
    assert f"/spaces/{board['space_id']}/tasks/{task_id}/versions?version=2" in second


# --- 谁在项目里读得到 `题目/`：题目的判据，不是项目的名册 ------------------------------


def _raw_in_project(api_client: TestClient, project_id: str, token: str, name: str):
    return api_client.get(
        f"/projects/{project_id}/library/raw",
        params={"path": f"题目/{name}"},
        headers=_auth(token),
    )


def _top_paths(api_client: TestClient, project_id: str, token: str) -> list[str]:
    resp = api_client.get(f"/projects/{project_id}/library", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return [row["path"] for row in resp.json()["data"]["data"]]


def test_a_board_manager_who_did_not_publish_may_download_without_claiming(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    """管理员是替出题人管这块板的人，不该为了看一眼材料先把自己报名上去。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="管理员要看材料的题")
    _add(api_client, task_id, board["creator_token"], "讲义.pdf", MATERIAL)

    manager = user_client.create_user()
    manager_token = _login(user_client, api_client, manager)
    resp = api_client.post(
        f"/spaces/{board['space_id']}/managers",
        json={"userId": manager.user_id, "role": "ADMIN"},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 201, resp.text

    assert _files(api_client, task_id, manager_token).json()["data"]["canDownload"]
    resp = _download(api_client, task_id, manager_token, "讲义.pdf")
    assert resp.status_code == 200
    assert resp.content == MATERIAL


def test_no_read_path_reaches_the_task_folder_before_the_claim_is_approved(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,
    db_session,
    _portal,
):
    """`题目/` 跟着项目来自的那份申请走：还在等通过时（早先的版本在申请时就开了
    项目，这样的项目还在），资料库的清单、搜索、读文本、取字节、`cheese library get`
    先要的下载地址都拿不到它；通过之后都拿得到。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="通过前读不到")
    _add(api_client, task_id, board["creator_token"], "数据.csv", b"secret\n")
    _, token = _member_of(user_client, api_client, board)
    participant = _claim(api_client, board, task_id, token)
    project_id = _project_from(api_client, task_id, token)

    async def back_to_waiting() -> None:
        claim = await db_session.get(TaskMembership, participant)
        claim.approved = 2
        await db_session.flush()

    _portal.call(back_to_waiting)
    headers = _auth(token)

    def reads() -> dict[str, int]:
        found = api_client.get(
            f"/projects/{project_id}/library", params={"q": "数据"}, headers=headers
        ).json()["data"]["data"]
        return {
            "listed": int("题目" in _top_paths(api_client, project_id, token)),
            "found": len(found),
            "raw": _raw_in_project(
                api_client, project_id, token, "数据.csv"
            ).status_code,
            "text": api_client.get(
                f"/projects/{project_id}/library/text",
                params={"path": "题目/数据.csv"},
                headers=headers,
            ).status_code,
            "link": api_client.get(
                f"/projects/{project_id}/library/link",
                params={"path": "题目/数据.csv"},
                headers=headers,
            ).status_code,
        }

    assert reads() == {"listed": 0, "found": 0, "raw": 403, "text": 403, "link": 403}

    resp = api_client.patch(
        f"/tasks/{task_id}/participants/{participant}",
        json={"approved": "APPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text
    assert reads() == {"listed": 1, "found": 1, "raw": 200, "text": 200, "link": 200}
    link = api_client.get(
        f"/projects/{project_id}/library/link",
        params={"path": "题目/数据.csv"},
        headers=headers,
    )
    assert api_client.get(link.json()["data"]["url"]).content == b"secret\n"


def test_the_materials_wait_for_the_application_to_be_approved(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    """材料只给参与者：申请还在等批的、被拒了的，题目页拿不到材料，也还没有从
    这道题来的项目；申请通过了，材料拿得到，开始做之后项目里的 `题目/` 也拿得到。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(
        api_client, board, name="通过了才拿得到材料的题", startMode="APPLY"
    )
    _add(api_client, task_id, board["creator_token"], "数据.csv", b"a,b\n")
    _, waiting = _member_of(user_client, api_client, board)
    _, declined = _member_of(user_client, api_client, board)
    waiting_claim = _claim(api_client, board, task_id, waiting, approve=False)
    declined_claim = _claim(api_client, board, task_id, declined, approve=False)
    resp = api_client.patch(
        f"/tasks/{task_id}/participants/{declined_claim}",
        json={"approved": "DISAPPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text

    for token in (waiting, declined):
        assert _files(api_client, task_id, token).json()["data"]["canDownload"] is False
        resp = _download(api_client, task_id, token, "数据.csv")
        assert resp.status_code == 403
        assert resp.json()["error"]["i18n"]["key"] == "taskFilesForTakers"
        page = api_client.get(f"/tasks/{task_id}", headers=_auth(token))
        assert page.json()["data"]["task"]["myProject"] is None

    resp = api_client.patch(
        f"/tasks/{task_id}/participants/{waiting_claim}",
        json={"approved": "APPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text
    assert _download(api_client, task_id, waiting, "数据.csv").content == b"a,b\n"
    project_id = _project_from(api_client, task_id, waiting)
    assert "题目" in _top_paths(api_client, project_id, waiting)


def test_a_claim_no_longer_valid_stops_the_task_reaching_its_project(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,
    db_session,
    _portal,
):
    """申请不再有效：项目还在，题目的材料（现在的和以后改的）读不到了，项目里的
    agent 也不再被告知题目。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="不再有效的申请")
    _add(api_client, task_id, board["creator_token"], "数据.csv", b"v1\n")
    _, token = _member_of(user_client, api_client, board)
    participant = _claim(api_client, board, task_id, token)
    project_id = _project_from(api_client, task_id, token)
    assert _raw_in_project(api_client, project_id, token, "数据.csv").status_code == 200

    resp = api_client.delete(
        f"/tasks/{task_id}/participants/{participant}",
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 204, resp.text
    _replace(api_client, task_id, board["creator_token"], "数据.csv", b"v2\n")

    assert _download(api_client, task_id, token, "数据.csv").status_code == 403
    assert _raw_in_project(api_client, project_id, token, "数据.csv").status_code == 403
    assert "题目" not in _top_paths(api_client, project_id, token)

    async def section() -> str | None:
        project = await db_session.get(Project, uuid.UUID(project_id))
        return await package.section_for_project(db_session, project)

    assert _portal.call(section) is None


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def test_the_taker_reads_the_task_files_everywhere_the_project_reads_its_library(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    """领了题的人在项目里读 `题目/` 的每一条路都读得到：资料库、房间里取文件
    （`cheese pull`）、复制一份到房间里改、项目文档里引用的图片。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="到处都读得到")
    _add(api_client, task_id, board["creator_token"], "数据.csv", b"a,b\n")
    _add(api_client, task_id, board["creator_token"], "示意.png", PNG)
    _, token = _member_of(user_client, api_client, board)
    _claim(api_client, board, task_id, token)
    project_id = _project_from(api_client, task_id, token)
    headers = _auth(token)

    assert _raw_in_project(api_client, project_id, token, "数据.csv").status_code == 200
    room = api_client.post(
        "/topics", json={"project_id": project_id, "title": "频道"}, headers=headers
    )
    assert room.status_code == 200, room.text
    room_id = room.json()["data"]["id"]

    pulled = api_client.get(
        f"/topics/{room_id}/files/raw",
        params={"path": "library/题目/数据.csv"},
        headers=headers,
    )
    assert pulled.status_code == 200, pulled.text
    copied = api_client.post(
        f"/topics/{room_id}/files/copy",
        json={"source": "library/题目/数据.csv", "path": "我的数据.csv"},
        headers=headers,
    )
    assert copied.status_code == 200, copied.text

    made = api_client.post(
        f"/projects/{project_id}/documents",
        json={"title": "笔记", "content": "![](library/题目/示意.png)\n"},
        headers=headers,
    )
    assert made.status_code == 200, made.text
    image = api_client.get(
        f"/documents/{made.json()['data']['id']}/image",
        params={"path": "library/题目/示意.png"},
        headers=headers,
    )
    assert image.status_code == 200, image.text
    assert image.content == PNG
