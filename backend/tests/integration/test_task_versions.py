"""A task's version history (#4767 §一): every version newest first, with when,
who, what changed and the files it held; each file downloadable as it was at
that version, by whoever may download the task's materials; and a project
made from the task says which task and version it is at while its `题目/`
folder is shown.

1. **Which file a version download returns**: the one that version held, by
   its number; another version's file of the same name is not it.
2. **A version recorded without its files** says so, the latest included:
   today's files are not passed off as what it held.
3. **The project's `task` field** follows the claim the project came from, as
   its `题目/` folder does — not the reader's own claims elsewhere.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import update

from app.domain.task import package
from app.domain.task.models import TaskPackageRevision
from tests.integration.conftest import UserCreator
from tests.integration.test_task_files import (
    MATERIAL,
    _add,
    _auth,
    _claim,
    _create_task,
    _member_of,
    _new_board,
    _project_from,
    _replace,
    _upload_loose,
    upload_root,  # noqa: F401  (fixture)
)
from tests.integration.test_task_materials_follow_the_claim import (
    _back_to_waiting,
    _claim_as_team,
    _decide,
    _join,
    _team,
)


def _versions(api_client: TestClient, task_id: int, token: str):
    return api_client.get(f"/tasks/{task_id}/versions", headers=_auth(token))


def _download_version(
    api_client: TestClient, task_id: int, token: str, name: str, version: int
):
    return api_client.get(
        f"/tasks/{task_id}/files/raw",
        params={"name": name, "version": version},
        headers=_auth(token),
    )


def _held(version: dict) -> dict[str, dict]:
    return {f["name"]: f for f in version["files"]}


def _project_task(api_client: TestClient, project_id: str, token: str):
    resp = api_client.get(f"/projects/{project_id}", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["task"]


def test_the_history_lists_each_version_with_the_files_it_held(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,  # noqa: F811
):
    """新的在前：第几版、谁改的、改了什么；每一版手上的文件，按那一版的字节取回。
    随题一起发出去的材料是第 1 版的。"""
    board = _new_board(user_client, api_client)
    token = board["creator_token"]
    picked = _upload_loose(api_client, token, "讲义.pdf", MATERIAL)
    task_id = _create_task(api_client, board, name="有历史的题", attachmentIds=[picked])
    assert _add(api_client, task_id, token, "数据.csv", b"v1").status_code == 201
    assert _replace(api_client, task_id, token, "数据.csv", b"v2").status_code == 200
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={"description": "说明改了"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text

    resp = _versions(api_client, task_id, token)
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["canDownload"] is True
    versions = body["versions"]
    assert [(v["number"], v["changes"]) for v in versions] == [
        (4, {"description": True}),
        (3, {"files": {"replaced": ["数据.csv"]}}),
        (2, {"files": {"added": ["数据.csv"]}}),
        (1, {"published": True}),
    ]
    assert {v["author"]["username"] for v in versions} == {board["creator"].username}
    assert set(_held(versions[3])) == {"讲义.pdf", package.BRIEF}
    assert set(_held(versions[2])) == {"讲义.pdf", "数据.csv", package.BRIEF}

    def bytes_of(number: int, name: str) -> bytes:
        resp = _download_version(api_client, task_id, token, name, number)
        assert resp.status_code == 200, resp.text
        return resp.content

    assert bytes_of(1, "讲义.pdf") == MATERIAL
    # 题目说明也是那一版的：第 1 版的说明写着第 1 版。
    assert "第 1 版" in bytes_of(1, package.BRIEF).decode()
    assert "说明改了" in bytes_of(4, package.BRIEF).decode()


def test_a_version_download_is_the_file_that_version_held(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,  # noqa: F811
):
    """同一个名字在第 2 版和第 3 版是两份：要哪一版给哪一版；那一版没有这份，就没有。"""
    board = _new_board(user_client, api_client)
    token = board["creator_token"]
    task_id = _create_task(api_client, board, name="同名两版")
    _add(api_client, task_id, token, "数据.csv", b"v1")
    _replace(api_client, task_id, token, "数据.csv", b"v2")

    assert _download_version(api_client, task_id, token, "数据.csv", 2).content == (
        b"v1"
    )
    assert _download_version(api_client, task_id, token, "数据.csv", 3).content == (
        b"v2"
    )
    # 第 1 版还没有这份；第 9 版不存在。
    for number in (1, 9):
        resp = _download_version(api_client, task_id, token, "数据.csv", number)
        assert resp.status_code == 404, resp.text


def test_a_material_deleted_later_keeps_its_name_in_the_versions_that_had_it(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,  # noqa: F811
):
    """删一份材料连同它的几版一起删，旧的版本里只剩名字，取不到。"""
    board = _new_board(user_client, api_client)
    token = board["creator_token"]
    task_id = _create_task(api_client, board, name="删过材料的题")
    _add(api_client, task_id, token, "草稿.txt", b"draft")
    resp = api_client.delete(
        f"/tasks/{task_id}/files", params={"name": "草稿.txt"}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text

    versions = _versions(api_client, task_id, token).json()["data"]["versions"]
    assert versions[0]["changes"] == {"files": {"removed": ["草稿.txt"]}}
    assert "草稿.txt" not in _held(versions[0])
    assert _held(versions[1])["草稿.txt"] == {"name": "草稿.txt", "size": None}
    resp = _download_version(api_client, task_id, token, "草稿.txt", 2)
    assert resp.status_code == 404, resp.text


def test_a_version_recorded_without_its_files_says_so_even_when_latest(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,  # noqa: F811
    db_session,
    _portal,
):
    """记版本时还没记文件的那一版（这一列加上之前的），就算它还是最新的一版，也照实
    说没记下，不拿现在的文件冒充它；它的文件也取不到。"""
    board = _new_board(user_client, api_client)
    token = board["creator_token"]
    task_id = _create_task(api_client, board, name="旧版本")
    _add(api_client, task_id, token, "数据.csv", b"a,b\n")

    async def forget() -> None:
        await db_session.execute(
            update(TaskPackageRevision)
            .where(TaskPackageRevision.task_id == task_id)
            .values(files=None)
        )
        await db_session.flush()

    _portal.call(forget)

    versions = _versions(api_client, task_id, token).json()["data"]["versions"]
    assert [(v["number"], v["files"]) for v in versions] == [(2, None), (1, None)]
    resp = _download_version(api_client, task_id, token, "数据.csv", 2)
    assert resp.status_code == 404, resp.text


def test_the_history_is_for_who_sees_the_task_and_its_files_for_who_took_it(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,  # noqa: F811
):
    """历史和材料清单同一道门：看得见题就看得到；各版的文件和现在的材料同一条下载判据。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="历史谁看得到")
    _add(api_client, task_id, board["creator_token"], "数据.csv", b"a,b\n")
    _, member_token = _member_of(user_client, api_client, board)

    resp = _versions(api_client, task_id, member_token)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["canDownload"] is False
    resp = _download_version(api_client, task_id, member_token, "数据.csv", 2)
    assert resp.status_code == 403

    _claim(api_client, board, task_id, member_token)
    resp = _download_version(api_client, task_id, member_token, "数据.csv", 2)
    assert resp.status_code == 200
    assert resp.content == b"a,b\n"

    hidden = _create_task(
        api_client, board, name="限可见范围的题", accessControlEnabled=True
    )
    assert _versions(api_client, hidden, member_token).status_code == 404


def test_the_project_names_its_task_while_its_task_folder_is_shown(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,  # noqa: F811
):
    """从题目来、申请已通过的项目说它来自哪道题、现在第几版（项目总览和 `题目/`
    由此连到题目页）。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="项目连到题目")
    _add(api_client, task_id, board["creator_token"], "数据.csv", b"a,b\n")
    _, taker_token = _member_of(user_client, api_client, board)
    _claim(api_client, board, task_id, taker_token)
    project_id = _project_from(api_client, task_id, taker_token)

    assert _project_task(api_client, project_id, taker_token) == {
        "id": task_id,
        "space_id": board["space_id"],
        "name": "项目连到题目",
        "version": 2,
    }


def test_the_project_task_field_follows_the_claim_the_project_came_from(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,  # noqa: F811
    db_session,
    _portal,
):
    """一个人在两个都申请了的团队里：通过了的那个团队的项目说它来自哪道题；还在等批
    的那个团队的项目（早先的版本在申请时建的）不说——对他、对那个团队的别人都一样，
    他别处的申请不算。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="两个团队", submitterType="TEAM")
    _add(api_client, task_id, board["creator_token"], "数据.csv", b"a,b\n")
    both, both_token = _member_of(user_client, api_client, board)
    _, other_token = _member_of(user_client, api_client, board)
    approved_team, _ = _team(api_client, both_token)
    waiting_team, _ = _team(api_client, other_token)
    approved_claim, _ = _claim_as_team(api_client, task_id, approved_team, both_token)
    waiting_claim, _ = _claim_as_team(api_client, task_id, waiting_team, other_token)
    _decide(api_client, board, task_id, approved_claim, "APPROVED")
    _decide(api_client, board, task_id, waiting_claim, "APPROVED")
    _, approved_project = _claim_as_team(api_client, task_id, approved_team, both_token)
    _, waiting_project = _claim_as_team(api_client, task_id, waiting_team, other_token)
    # The waiting team's project is one an earlier release opened on application.
    _back_to_waiting(db_session, _portal, waiting_claim)
    _join(db_session, _portal, waiting_team, both)

    assert _project_task(api_client, approved_project, both_token)["id"] == task_id
    assert _project_task(api_client, waiting_project, both_token) is None
    assert _project_task(api_client, waiting_project, other_token) is None
