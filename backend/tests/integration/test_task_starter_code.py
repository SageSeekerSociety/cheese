"""题目的起始代码 (#4767 第二节)：出题人给一个 .zip，领题开出来的项目，代码仓库的第一个
提交就是它解开的样子。

1. 压缩包在上传那一刻就查：解不开的、路径越出目录的、带链接的、太多太大的、压缩炸弹，
   当场拒绝，什么都不留下；
2. 领题开项目时取的是那一刻的那一份，之后换压缩包只影响之后领题的人；
3. 项目记得起始代码来自题目第几版，项目里的 agent 也被告知；
4. 没有起始代码的题，开出来的项目和从前一样。

这里的仓库是测试的本地 git 仓库（`stub_project_forge`），推送走的是真正推送起始代码
的那段代码；真的 Forgejo 那一半在 `tests/forgejo/test_live.py`。
"""

from __future__ import annotations

import io
import stat
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.domain.project.models import Project
from app.domain.task import package
from tests.integration.conftest import UserCreator
from tests.integration.test_task_files import (
    _add,
    _auth,
    _create_task,
    _files,
    _member_of,
    _post_task,
    _put,
    _upload_loose,
    upload_root,  # noqa: F401 — the fixture every test here uses (pytestmark)
)
from tests.integration.test_task_files import _new_board as new_board
from tests.support import git_store

#: 字节落在 tmp_path：`test_task_files` 的同一个夹具。
pytestmark = pytest.mark.usefixtures("upload_root")


def _zip(*entries: tuple[str, bytes] | zipfile.ZipInfo, data: bytes = b"") -> bytes:
    """一个压缩包：``(路径, 内容)`` 是普通文件，``ZipInfo`` 原样写进去（内容是 ``data``）。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            if isinstance(entry, zipfile.ZipInfo):
                archive.writestr(entry, data)
            else:
                archive.writestr(*entry)
    return buffer.getvalue()


def _executable(path: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o755) << 16
    return info


def _symlink(path: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path)
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


def _lab(readme: bytes = b"# Lab 1\n") -> bytes:
    """右键压缩一个文件夹会得到的那种包：一个顶层文件夹，外加 macOS 塞进来的东西。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("lab1/README.md", readme)
        archive.writestr(_executable("lab1/run.sh"), b"#!/bin/sh\necho hi\n")
        archive.writestr("lab1/src/main.py", "print('起始代码')\n")
        archive.writestr("lab1/.DS_Store", b"\0\0")
        archive.writestr("__MACOSX/lab1/._README.md", b"\0")
    return buffer.getvalue()


def _set_starter(api_client: TestClient, task_id: int, token: str, data: bytes):
    refused, upload = _put(api_client, task_id, token, package.STARTER, data)
    if refused is not None:
        return refused
    return api_client.put(
        f"/tasks/{task_id}/starter", json=upload, headers=_auth(token)
    )


def _claim_project(api_client: TestClient, task_id: int, token: str) -> uuid.UUID:
    """领题：领题这一下就开出了项目。"""
    resp = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    return uuid.UUID(resp.json()["data"]["project"]["id"])


def _main(project_id: uuid.UUID) -> dict[str, str]:
    """仓库 main 上的每一份文件和它的模式。"""
    listing = git_store.git(git_store.path(project_id), "ls-tree", "-r", "main")
    return {line.split("\t", 1)[1]: line.split()[0] for line in listing.splitlines()}


def _show(project_id: uuid.UUID, what: str) -> str:
    return git_store.git(git_store.path(project_id), "show", what)


def _log(project_id: uuid.UUID) -> list[str]:
    return git_store.git(
        git_store.path(project_id), "log", "--format=%an <%ae>|%s", "main"
    ).splitlines()


def _starter_revision(api_client: TestClient, project_id: uuid.UUID, token: str):
    resp = api_client.get(f"/projects/{project_id}", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["starter_revision"]


# --- 领题时 -------------------------------------------------------------------------


def test_a_claimed_task_s_repository_begins_with_its_starter_code(
    api_client: TestClient, user_client: UserCreator
):
    board = new_board(user_client, api_client)
    starter = _upload_loose(api_client, board["creator_token"], "lab1.zip", _lab())
    task_id = _create_task(
        api_client, board, name="带起始代码的题", starterAttachmentId=starter
    )
    _, member_token = _member_of(user_client, api_client, board)

    project_id = _claim_project(api_client, task_id, member_token)

    # 顶层文件夹去掉了，压缩工具塞进来的东西不在，可执行位留着。
    assert _main(project_id) == {
        "README.md": "100644",
        "run.sh": "100755",
        "src/main.py": "100644",
    }
    assert _show(project_id, "main:src/main.py") == "print('起始代码')\n"
    # 只有这一个提交，作者是平台，写着来自哪道题的第几版。
    assert _log(project_id) == [
        "Cheese <starter@users.invalid>|Starter code from task 带起始代码的题 (version 1)"
    ]
    assert _starter_revision(api_client, project_id, member_token) == 1


def test_a_new_starter_reaches_only_those_who_claim_after_it(
    api_client: TestClient, user_client: UserCreator
):
    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="中途换了起始代码的题")
    resp = _set_starter(api_client, task_id, board["creator_token"], _lab(b"v1\n"))
    assert resp.status_code == 200, resp.text
    _, early_token = _member_of(user_client, api_client, board)
    early = _claim_project(api_client, task_id, early_token)

    resp = _set_starter(api_client, task_id, board["creator_token"], _lab(b"v2\n"))
    assert resp.status_code == 200, resp.text
    _, late_token = _member_of(user_client, api_client, board)
    late = _claim_project(api_client, task_id, late_token)

    assert _show(early, "main:README.md") == "v1\n"
    assert _show(late, "main:README.md") == "v2\n"
    # 发布是第 1 版，放进起始代码第 2 版，换掉它第 3 版。
    assert _starter_revision(api_client, early, early_token) == 2
    assert _starter_revision(api_client, late, late_token) == 3


def test_a_task_without_starter_code_opens_projects_as_before(
    api_client: TestClient, user_client: UserCreator
):
    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="没有起始代码的题")
    _, member_token = _member_of(user_client, api_client, board)

    project_id = _claim_project(api_client, task_id, member_token)

    assert _starter_revision(api_client, project_id, member_token) is None
    assert _main(project_id) == {}
    assert not any(line.startswith("Cheese ") for line in _log(project_id))


def test_the_projects_agents_are_told_where_the_first_commit_came_from(
    api_client: TestClient,
    user_client: UserCreator,
    db_session,
    _portal,
):
    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="agent 要知道起始代码")
    _set_starter(api_client, task_id, board["creator_token"], _lab())
    _, member_token = _member_of(user_client, api_client, board)
    project_id = _claim_project(api_client, task_id, member_token)

    async def read() -> str | None:
        project = await db_session.get(Project, project_id)
        return await package.section_for_project(db_session, project)

    section = _portal.call(read)
    assert section is not None
    assert "第 2 版的起始代码" in section
    assert "新增起始代码" in section


# --- 出题人放进、换掉、删掉 ----------------------------------------------------------


def test_the_starter_has_its_own_place_beside_the_materials(
    api_client: TestClient, user_client: UserCreator
):
    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="起始代码不在材料里")
    _add(api_client, task_id, board["creator_token"], "数据.csv", b"a,b\n")
    archive = _lab()
    assert (
        _set_starter(api_client, task_id, board["creator_token"], archive).status_code
        == 200
    )

    body = _files(api_client, task_id, board["creator_token"]).json()["data"]
    assert [f["name"] for f in body["files"]] == ["数据.csv"]
    assert body["starter"]["name"] == package.STARTER
    assert body["starter"]["size"] == len(archive)

    # 材料那一栏碰不到它：换、删、拿这个名字新放一份都不行。
    for refused in (
        _add(api_client, task_id, board["creator_token"], package.STARTER, archive),
        api_client.delete(
            f"/tasks/{task_id}/files",
            params={"name": package.STARTER},
            headers=_auth(board["creator_token"]),
        ),
    ):
        assert refused.status_code == 422, refused.text

    removed = api_client.delete(
        f"/tasks/{task_id}/starter", headers=_auth(board["creator_token"])
    )
    assert removed.status_code == 200, removed.text
    body = _files(api_client, task_id, board["creator_token"]).json()["data"]
    assert body["starter"] is None


def test_only_the_publisher_or_a_manager_sets_the_starter(
    api_client: TestClient, user_client: UserCreator
):
    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="别人不能换起始代码")
    _, member_token = _member_of(user_client, api_client, board)

    assert _set_starter(api_client, task_id, member_token, _lab()).status_code == 403
    assert (
        api_client.delete(
            f"/tasks/{task_id}/starter", headers=_auth(member_token)
        ).status_code
        == 403
    )


# --- 上传时就拒绝的压缩包 ------------------------------------------------------------


_TRAVERSAL = _zip(("lab/ok.txt", b"ok"), ("lab/../../evil.txt", b"evil"))
_ABSOLUTE = _zip(("/etc/cron.d/evil", b"evil"))
_LINK = _zip(("lab/ok.txt", b"ok"), _symlink("lab/passwd"), data=b"/etc/passwd")
_BOMB = _zip(("zeros.bin", bytes(80 * 1024 * 1024)))


@pytest.mark.parametrize(
    ("archive", "key"),
    [
        (b"not a zip at all", "starterNotZip"),
        (_zip(), "starterEmpty"),
        (_TRAVERSAL, "starterUnsafeEntry"),
        (_ABSOLUTE, "starterUnsafeEntry"),
        (_LINK, "starterUnsafeEntry"),
        (_BOMB, "starterCompressionRatio"),
    ],
    ids=["not-zip", "empty", "traversal", "absolute", "symlink", "bomb"],
)
def test_a_bad_archive_is_refused_when_it_is_uploaded(
    api_client: TestClient,
    user_client: UserCreator,
    archive: bytes,
    key: str,
):
    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="坏压缩包")

    resp = _set_starter(api_client, task_id, board["creator_token"], archive)

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["i18n"]["key"] == key
    assert (
        _files(api_client, task_id, board["creator_token"]).json()["data"]["starter"]
        is None
    )


def test_an_archive_past_the_file_count_or_size_limit_is_refused(
    api_client: TestClient,
    user_client: UserCreator,
    monkeypatch: pytest.MonkeyPatch,
):
    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="太大的压缩包")
    three = _zip(("a.txt", b"1"), ("b.txt", b"2"), ("c.txt", b"3"))

    monkeypatch.setattr(settings, "starter_max_files", 2)
    resp = _set_starter(api_client, task_id, board["creator_token"], three)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["i18n"]["key"] == "starterTooManyFiles"

    monkeypatch.setattr(settings, "starter_max_files", 20_000)
    monkeypatch.setattr(settings, "starter_max_bytes", 2)
    resp = _set_starter(api_client, task_id, board["creator_token"], three)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["i18n"]["key"] == "starterTooLarge"


def test_a_bad_archive_picked_on_the_publish_page_publishes_nothing(
    api_client: TestClient, user_client: UserCreator
):
    board = new_board(user_client, api_client)
    bad = _upload_loose(api_client, board["creator_token"], "lab.zip", _TRAVERSAL)

    resp = _post_task(
        api_client, board, name="带坏起始代码的题", starterAttachmentId=bad
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["i18n"]["key"] == "starterUnsafeEntry"
