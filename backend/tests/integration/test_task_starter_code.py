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
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.core.config import settings
from app.domain.attachment.models import Attachment
from app.domain.project.models import Project, ProjectStarterPush
from app.domain.task import package, starter_jobs
from tests.integration.conftest import UserCreator
from tests.integration.test_task_files import (
    _add,
    _auth,
    _files,
    _member_of,
    _post_task,
    _put,
    _upload_loose,
    upload_root,  # noqa: F401 — the fixture every test here uses (pytestmark)
)
from tests.integration.test_task_files import _create_task as _create_task_of
from tests.integration.test_task_files import _new_board as new_board
from tests.support import git_store

#: 字节落在 tmp_path：`test_task_files` 的同一个夹具。
pytestmark = pytest.mark.usefixtures("upload_root")


def _create_task(api_client: TestClient, board: dict, **more) -> int:
    """直接开始的题：领题这一下就通过了、开出了项目，起始代码推进那个项目。"""
    return _create_task_of(api_client, board, **{"startMode": "DIRECT", **more})


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
    """领题：直接开始的题，领题这一下就通过了、开出了项目。"""
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


def _starter(api_client: TestClient, project_id: uuid.UUID, token: str):
    """项目说它的代码仓库从哪一版起始代码开始、放好了没有。"""
    resp = api_client.get(f"/projects/{project_id}", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["starter"]


@pytest.fixture
def push_starters(db_session, _portal):
    """跑一遍后台那件事：把领题记下的起始代码放进仓库，直到没有在等的。"""

    @asynccontextmanager
    async def sessions():
        yield db_session

    def run() -> dict[str, int]:
        return _portal.call(starter_jobs.push_due, sessions)

    return run


# --- 领题时 -------------------------------------------------------------------------


def test_a_claimed_task_s_repository_begins_with_its_starter_code(
    api_client: TestClient, user_client: UserCreator, push_starters
):
    board = new_board(user_client, api_client)
    starter = _upload_loose(api_client, board["creator_token"], "lab1.zip", _lab())
    task_id = _create_task(
        api_client, board, name="带起始代码的题", starterAttachmentId=starter
    )
    _, member_token = _member_of(user_client, api_client, board)

    project_id = _claim_project(api_client, task_id, member_token)
    # 领题当场就回来了：仓库在后台放起始代码，项目说它在准备。
    assert _starter(api_client, project_id, member_token)["state"] == "pending"

    assert push_starters()["done"] == 1
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
    assert _starter(api_client, project_id, member_token) == {
        "revision": 1,
        "state": "done",
        "error": None,
    }


def test_a_new_starter_reaches_only_those_who_claim_after_it(
    api_client: TestClient, user_client: UserCreator, push_starters
):
    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="中途换了起始代码的题")
    resp = _set_starter(api_client, task_id, board["creator_token"], _lab(b"v1\n"))
    assert resp.status_code == 200, resp.text
    _, early_token = _member_of(user_client, api_client, board)
    early = _claim_project(api_client, task_id, early_token)

    # 第一个人的仓库还没放好，出题人就换了一份：他拿到的仍是领题那一刻的那份。
    resp = _set_starter(api_client, task_id, board["creator_token"], _lab(b"v2\n"))
    assert resp.status_code == 200, resp.text
    _, late_token = _member_of(user_client, api_client, board)
    late = _claim_project(api_client, task_id, late_token)
    push_starters()

    assert _show(early, "main:README.md") == "v1\n"
    assert _show(late, "main:README.md") == "v2\n"
    # 发布是第 1 版，放进起始代码第 2 版，换掉它第 3 版。
    assert _starter(api_client, early, early_token)["revision"] == 2
    assert _starter(api_client, late, late_token)["revision"] == 3


def test_a_starter_removed_before_its_push_fails_saying_so(
    api_client: TestClient, user_client: UserCreator, push_starters
):
    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="领了之后删了起始代码的题")
    _set_starter(api_client, task_id, board["creator_token"], _lab())
    _, member_token = _member_of(user_client, api_client, board)
    project_id = _claim_project(api_client, task_id, member_token)
    removed = api_client.delete(
        f"/tasks/{task_id}/starter", headers=_auth(board["creator_token"])
    )
    assert removed.status_code == 200, removed.text

    assert push_starters()["failed"] == 1
    starter = _starter(api_client, project_id, member_token)
    assert starter["state"] == "failed"
    assert starter["error"]["key"] == "starterRemovedBeforePush"
    # 不会再试：再跑一遍什么都不做。
    assert push_starters() == {"done": 0, "failed": 0, "retried": 0, "lost": 0}


def test_a_task_without_starter_code_opens_projects_as_before(
    api_client: TestClient, user_client: UserCreator
):
    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="没有起始代码的题")
    _, member_token = _member_of(user_client, api_client, board)

    project_id = _claim_project(api_client, task_id, member_token)

    assert _starter(api_client, project_id, member_token) is None
    assert _main(project_id) == {}
    assert not any(line.startswith("Cheese ") for line in _log(project_id))


def test_the_projects_agents_are_told_where_the_first_commit_came_from(
    api_client: TestClient,
    user_client: UserCreator,
    db_session,
    _portal,
    push_starters,
):
    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="agent 要知道起始代码")
    _set_starter(api_client, task_id, board["creator_token"], _lab())
    _, member_token = _member_of(user_client, api_client, board)
    # The agents read the task once the claim is approved.
    project_id = _claim_project(api_client, task_id, member_token)

    async def read() -> str | None:
        project = await db_session.get(Project, project_id)
        return await package.section_for_project(db_session, project)

    before = _portal.call(read)
    assert before is not None
    assert "正在放入题目第 2 版的起始代码" in before
    push_starters()
    after = _portal.call(read)
    assert after is not None
    assert "第 2 版的起始代码，由平台提交" in after
    assert "新增起始代码" in after


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


def _attachments(db_session, portal) -> int:
    """附件表里一共有几份。"""
    return portal.call(db_session.scalar, select(func.count()).select_from(Attachment))


def _send_starter(api_client: TestClient, token: str, data: bytes):
    """发题页那条路：选完起始代码就交上来，查过才留下。"""
    return api_client.post(
        "/task-drafts/starter",
        headers=_auth(token),
        files={"file": ("lab.zip", io.BytesIO(data), "application/zip")},
    )


def test_a_starter_refused_on_the_publish_page_leaves_nothing_stored(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    """发题页上选完压缩包就查：不合格的当场说原因（和放进题目时同一句），什么都不存下；
    合格的存成这个人的一份附件，交回它的 id。"""
    board = new_board(user_client, api_client)
    token = board["creator_token"]
    before = _attachments(db_session, _portal)

    refused = _send_starter(api_client, token, _TRAVERSAL)

    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "starterUnsafeEntry"
    assert _attachments(db_session, _portal) == before

    accepted = _send_starter(api_client, token, _lab())
    assert accepted.status_code == 201, accepted.text
    assert _attachments(db_session, _portal) == before + 1


def test_a_draft_refuses_someone_elses_upload_as_its_starter(
    api_client: TestClient, user_client: UserCreator
):
    """草稿只存这个人自己传的起始代码：别人的附件在存的那一下就拒，不等到发布；
    自己的照存。"""
    board = new_board(user_client, api_client)
    token = board["creator_token"]
    _, stranger = _member_of(user_client, api_client, board)
    theirs = _upload_loose(api_client, stranger, "lab.zip", _lab())
    mine = _upload_loose(api_client, token, "lab.zip", _lab())
    draft = api_client.post(
        "/task-drafts", json={"space": board["space_id"]}, headers=_auth(token)
    ).json()["data"]

    foreign = api_client.patch(
        f"/task-drafts/{draft['id']}",
        json={"fields": {"starterAttachmentId": theirs}},
        headers=_auth(token),
    )
    assert foreign.status_code == 403, foreign.text
    kept = api_client.get(f"/task-drafts/{draft['id']}", headers=_auth(token))
    assert kept.json()["data"]["fields"].get("starterAttachmentId") is None

    own = api_client.patch(
        f"/task-drafts/{draft['id']}",
        json={"fields": {"starterAttachmentId": mine}},
        headers=_auth(token),
    )
    assert own.status_code == 200, own.text


# --- 上传时收下的，领题时一定放得进仓库 ---------------------------------------------


def _with_method(data: bytes, method: int) -> bytes:
    """同一个压缩包，每一项都标成用 ``method`` 压缩的（本地头和目录里各一处）。"""
    raw = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        at = archive.start_dir
    for info in infos:
        start = info.header_offset + 8
        raw[start : start + 2] = method.to_bytes(2, "little")
    for _ in infos:
        raw[at + 10 : at + 12] = method.to_bytes(2, "little")
        name = int.from_bytes(raw[at + 28 : at + 30], "little")
        extra = int.from_bytes(raw[at + 30 : at + 32], "little")
        comment = int.from_bytes(raw[at + 32 : at + 34], "little")
        at += 46 + name + extra + comment
    return bytes(raw)


def _named(name: bytes) -> bytes:
    """一个压缩包，里面一份文件的名字原样是 ``name`` 这些字节。"""
    placeholder = b"y" * len(name)
    data = _zip(("lab/ok.txt", b"ok"), (placeholder.decode(), b"x"))
    return data.replace(placeholder, name)


@pytest.mark.parametrize(
    ("archive", "key"),
    [
        (_zip(("lab/GIT~1/HEAD", b"ref"), ("lab/ok.txt", b"ok")), "starterNameInvalid"),
        (_zip(("lab/.git /config", b"x"), ("lab/ok.txt", b"ok")), "starterNameInvalid"),
        (_with_method(_zip(("lab/ok.txt", b"ok")), 9), "starterCompressionUnsupported"),
        (_zip(("lab/" + "名" * 86 + ".txt", b"x")), "starterNameInvalid"),
        (_named(b"lab/a\x00b.txt"), "starterNameInvalid"),
    ],
    ids=["git-short-name", "git-trailing-space", "deflate64", "long-name", "nul"],
)
def test_an_archive_a_claim_could_not_unpack_is_refused_at_upload(
    api_client: TestClient, user_client: UserCreator, archive: bytes, key: str
):
    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="领题时会坏的压缩包")

    resp = _set_starter(api_client, task_id, board["creator_token"], archive)

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["i18n"]["key"] == key
    listed = _files(api_client, task_id, board["creator_token"]).json()["data"]
    assert listed["starter"] is None


def test_a_repository_folder_in_any_case_is_left_out_and_the_claim_lands(
    api_client: TestClient, user_client: UserCreator, push_starters
):
    """压缩整个仓库文件夹会带上版本库自己的那一层；不区分大小写的盘上 `.GIT` 就是它。"""
    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="带着版本库那一层的压缩包")
    archive = _zip(
        ("lab/.GIT/HEAD", b"ref: refs/heads/evil\n"),
        ("lab/src/.Git/config", b"[core]\n"),
        ("lab/main.py", b"print(1)\n"),
    )
    resp = _set_starter(api_client, task_id, board["creator_token"], archive)
    assert resp.status_code == 200, resp.text
    _, member_token = _member_of(user_client, api_client, board)
    project_id = _claim_project(api_client, task_id, member_token)

    assert push_starters()["done"] == 1
    assert _main(project_id) == {"main.py": "100644"}


def test_the_ai_teammate_removes_a_published_tasks_starter_code(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    """芝士在改已发布题目的草稿里写 `starter: 0`，保存之后这道题就没有起始代码了，
    并且记一版。"""
    from app.core.sandbox_auth import mint_delegated_credential

    board = new_board(user_client, api_client)
    attachment = _upload_loose(api_client, board["creator_token"], "lab.zip", _lab())
    task_id = _create_task(
        api_client,
        board,
        name="要去掉起始代码的题",
        rank=2,
        starterAttachmentId=attachment,
    )
    listed = _files(api_client, task_id, board["creator_token"]).json()["data"]
    assert listed["starter"] is not None
    creator = board["creator"]
    headers = {
        "Authorization": f"Bearer {
            mint_delegated_credential(
                user_id=creator.user_id,
                handle=creator.username,
                work=str(uuid.uuid4()),
                ttl_s=300,
                read_only=False,
            )
        }"
    }

    written = api_client.post(
        "/task-drafts/agent/write",
        json={"task": task_id, "starter": 0},
        headers=headers,
    )
    assert written.status_code == 200, written.text
    published = api_client.post(
        "/task-drafts/agent/publish",
        json={"draft": written.json()["data"]["draft"]},
        headers=headers,
    )
    assert published.status_code == 200, published.text

    listed = _files(api_client, task_id, board["creator_token"]).json()["data"]
    assert listed["starter"] is None
    latest = _portal.call(package.current, db_session, task_id)
    assert latest is not None
    assert latest.changes[package.STARTER_CHANGE] == package.REMOVED


# --- 后台放代码：放成了才算放成，没放成说清为什么 -----------------------------------


def test_a_path_as_long_as_upload_allows_lands_in_the_repository(
    api_client: TestClient, user_client: UserCreator, push_starters
):
    """上传时收下的最长路径（4095 字节），领题后照样进仓库，不因为后台在哪个目录干活
    而变长、然后永远放不进去。"""
    long = "/".join(["n" * 254 + str(i % 10) for i in range(16)])
    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="路径很长的起始代码")
    archive = _zip((long, b"deep"), ("README.md", b"readme"))
    resp = _set_starter(api_client, task_id, board["creator_token"], archive)
    assert resp.status_code == 200, resp.text
    _, member_token = _member_of(user_client, api_client, board)
    project_id = _claim_project(api_client, task_id, member_token)

    assert push_starters()["done"] == 1
    assert set(_main(project_id)) == {long, "README.md"}


async def _due_now(session, project_id: uuid.UUID) -> None:
    await session.execute(
        update(ProjectStarterPush)
        .where(ProjectStarterPush.project_id == project_id)
        .values(retry_at=None)
    )
    await session.commit()


def test_a_push_whose_answer_was_lost_is_done_even_if_the_starter_is_gone(
    api_client: TestClient,
    user_client: UserCreator,
    push_starters,
    monkeypatch,
    db_session,
    _portal,
):
    """推送其实落地了、只是回答没回来；之后出题人删了起始代码。下一次先问仓库：
    起始代码已经在 main 上，就是放好了。"""
    from app.core.errors import GatewayUnavailableError
    from app.domain.project import starter as starter_archive

    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="回答丢了的推送")
    _set_starter(api_client, task_id, board["creator_token"], _lab(b"original\n"))
    _, member_token = _member_of(user_client, api_client, board)
    project_id = _claim_project(api_client, task_id, member_token)

    real_git = starter_archive._git

    def answer_lost(work, *args, env):
        out = real_git(work, *args, env=env)
        if args[0] == "push":
            raise GatewayUnavailableError("the answer never came back")
        return out

    monkeypatch.setattr(starter_archive, "_git", answer_lost)
    assert push_starters()["retried"] == 1
    monkeypatch.setattr(starter_archive, "_git", real_git)
    removed = api_client.delete(
        f"/tasks/{task_id}/starter", headers=_auth(board["creator_token"])
    )
    assert removed.status_code == 200, removed.text
    # The retry's wait is over.
    _portal.call(_due_now, db_session, project_id)

    second = push_starters()
    assert second["done"] == 1, (second, _starter(api_client, project_id, member_token))
    assert _show(project_id, "main:README.md") == "original\n"
    assert _starter(api_client, project_id, member_token)["state"] == "done"


def test_a_main_made_before_the_push_is_not_called_the_starter(
    api_client: TestClient, user_client: UserCreator, push_starters
):
    """领题之后、起始代码放进去之前，有写权限的人（学生或他的 agent）先往 main 推了
    别的提交：起始代码没有放进去，就照实这么说，不说放好了。"""
    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="main 先被占了")
    _set_starter(api_client, task_id, board["creator_token"], _lab())
    _, member_token = _member_of(user_client, api_client, board)
    project_id = _claim_project(api_client, task_id, member_token)
    repo = git_store.path(project_id)
    empty_tree = git_store.git(repo, "hash-object", "-t", "tree", "-w", "/dev/null")
    work = git_store.git(
        repo,
        "-c",
        "user.name=Student",
        "-c",
        "user.email=student@example.test",
        "commit-tree",
        empty_tree.strip(),
        "-m",
        "Student work",
    )
    git_store.git(repo, "update-ref", "refs/heads/main", work.strip())

    assert push_starters()["failed"] == 1
    starter = _starter(api_client, project_id, member_token)
    assert starter["state"] == "failed"
    assert starter["error"]["key"] == "starterMainTaken"
    assert _log(project_id) == ["Student <student@example.test>|Student work"]


# --- 压缩包的内容坏了：永远放不进去，照实说 ----------------------------------------


def _wrong_crc(data: bytes) -> bytes:
    """The same archive with every entry's CRC in the directory off by one: the
    directory reads fine, the bytes do not check out."""
    raw = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        count, at = len(archive.infolist()), archive.start_dir
    for _ in range(count):
        crc = int.from_bytes(raw[at + 16 : at + 20], "little")
        raw[at + 16 : at + 20] = ((crc + 1) % 2**32).to_bytes(4, "little")
        name = int.from_bytes(raw[at + 28 : at + 30], "little")
        extra = int.from_bytes(raw[at + 30 : at + 32], "little")
        comment = int.from_bytes(raw[at + 32 : at + 34], "little")
        at += 46 + name + extra + comment
    return bytes(raw)


def _broken_deflate(data: bytes) -> bytes:
    """The same archive with the middle of its first entry's compressed bytes
    overwritten."""
    raw = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info = archive.infolist()[0]
    name = int.from_bytes(
        raw[info.header_offset + 26 : info.header_offset + 28], "little"
    )
    extra = int.from_bytes(
        raw[info.header_offset + 28 : info.header_offset + 30], "little"
    )
    start = info.header_offset + 30 + name + extra
    middle = start + info.compress_size // 2
    raw[middle : middle + 16] = b"\xff" * 16
    return bytes(raw)


_TEXT = "".join(f"line {i} of the starter\n" for i in range(4000)).encode()


@pytest.mark.parametrize(
    "archive",
    [
        _wrong_crc(_zip(("lab/main.py", _TEXT))),
        _broken_deflate(_zip(("lab/main.py", _TEXT))),
    ],
    ids=["wrong-crc", "broken-deflate"],
)
def test_an_archive_whose_bytes_are_broken_fails_for_good_saying_so(
    api_client: TestClient, user_client: UserCreator, push_starters, archive: bytes
):
    """上传时只读目录，内容坏了到放进仓库时才读得出来：那时照实说压缩包解不开，不再
    重试，也不说「稍后重试」。"""
    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="内容坏了的压缩包")
    resp = _set_starter(api_client, task_id, board["creator_token"], archive)
    assert resp.status_code == 200, resp.text
    _, member_token = _member_of(user_client, api_client, board)
    project_id = _claim_project(api_client, task_id, member_token)

    assert push_starters()["failed"] == 1
    starter = _starter(api_client, project_id, member_token)
    assert starter["state"] == "failed"
    assert starter["error"]["key"] == "starterNotZip"
    assert push_starters() == {"done": 0, "failed": 0, "retried": 0, "lost": 0}


def test_the_archives_own_gitignore_files_keep_ignored_files_out(
    api_client: TestClient, user_client: UserCreator, push_starters
):
    """出题人把自己的工作文件夹原样压缩：它的 `.gitignore`（顶层的、文件夹里的）挡掉的
    `.env`、`node_modules/`、构建产物不进每个学生的仓库，和他自己提交时一样。"""
    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="带着 .gitignore 的文件夹")
    archive = _zip(
        ("lab/.gitignore", b".env\nnode_modules/\nbuild/\n"),
        ("lab/.env", b"SECRET=1\n"),
        ("lab/node_modules/left-pad/index.js", b"module.exports = 1\n"),
        ("lab/build/out.bin", b"\0\0"),
        ("lab/README.md", b"# Lab\n"),
        ("lab/src/.gitignore", b"*.tmp\n!keep.tmp\n"),
        ("lab/src/main.py", b"print(1)\n"),
        ("lab/src/scratch.tmp", b"x"),
        ("lab/src/keep.tmp", b"y"),
    )
    resp = _set_starter(api_client, task_id, board["creator_token"], archive)
    assert resp.status_code == 200, resp.text
    _, member_token = _member_of(user_client, api_client, board)
    project_id = _claim_project(api_client, task_id, member_token)

    assert push_starters()["done"] == 1
    assert set(_main(project_id)) == {
        ".gitignore",
        "README.md",
        "src/.gitignore",
        "src/main.py",
        "src/keep.tmp",
    }


# --- 领取的项目 id 随机、猜不到；一个团队的领取不会撞上它以前的项目 -----------------


def _claim_and_withdraw_later(
    api_client: TestClient, task_id: int, token: str
) -> tuple[uuid.UUID, int]:
    """领题：开出的项目，和撤回时要用的那一条领取。"""
    resp = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    return uuid.UUID(data["project"]["id"]), data["participant"]["id"]


def _withdraw(api_client: TestClient, task_id: int, participant: int, token: str):
    gone = api_client.delete(
        f"/tasks/{task_id}/participants/{participant}", headers=_auth(token)
    )
    assert gone.status_code == 204, gone.text


def test_a_claim_after_the_first_project_moved_to_another_team_opens_a_new_one(
    api_client: TestClient, user_client: UserCreator
):
    """领题开的项目被搬进了另一个团队；撤回再领，领到的是一个新项目，不撞上搬走的那个。"""
    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="项目搬走后再领")
    _, member_token = _member_of(user_client, api_client, board)
    first, claim = _claim_and_withdraw_later(api_client, task_id, member_token)
    team = api_client.post(
        "/teams",
        json={"name": f"队 {uuid.uuid4().hex[:6]}", "intro": "队", "avatarId": 1},
        headers=_auth(member_token),
    )
    assert team.status_code == 201, team.text
    moved = api_client.post(
        f"/projects/{first}/move",
        json={"team_id": team.json()["data"]["team"]["id"]},
        headers=_auth(member_token),
    )
    assert moved.status_code == 200, moved.text
    _withdraw(api_client, task_id, claim, member_token)

    again = _claim_project(api_client, task_id, member_token)

    assert again != first


def test_a_claim_after_the_first_project_was_handed_over_opens_a_new_one(
    api_client: TestClient, user_client: UserCreator
):
    """领题开的项目交给了别人；撤回再领，领到的是一个新项目。"""
    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="项目交出去后再领")
    _, member_token = _member_of(user_client, api_client, board)
    first, claim = _claim_and_withdraw_later(api_client, task_id, member_token)
    heir = user_client.create_user()
    heir_token = user_client.login(api_client, heir.username, heir.password)
    # Only someone in the project can be handed it.
    invited = api_client.post(
        f"/projects/{first}/invitations",
        json={"user_handle": heir.username},
        headers=_auth(member_token),
    )
    assert invited.status_code == 200, invited.text
    joined = api_client.post(
        f"/invitations/{invited.json()['data']['id']}/respond",
        json={"accept": True},
        headers=_auth(heir_token),
    )
    assert joined.status_code == 200, joined.text
    offered = api_client.post(
        f"/projects/{first}/transfer",
        json={"to_handle": heir.username},
        headers=_auth(member_token),
    )
    assert offered.status_code == 200, offered.text
    taken = api_client.post(
        f"/transfers/{offered.json()['data']['id']}/respond",
        json={"accept": True},
        headers=_auth(heir_token),
    )
    assert taken.status_code == 200, taken.text
    _withdraw(api_client, task_id, claim, member_token)

    again = _claim_project(api_client, task_id, member_token)

    assert again != first


def test_nobody_can_take_a_claims_project_id_ahead_of_it(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    """项目 id 不由题目和团队推得出来：别人抢先用推算出的 id 建一个项目，也挡不住
    这个团队领题。（推算的办法是一度用过的那一个。）"""
    from app.domain.team.services import team_service

    board = new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="项目 id 被人抢先")
    student, member_token = _member_of(user_client, api_client, board)

    async def personal_team() -> int:
        team = await team_service(db_session).ensure_personal_team(student.user_id)
        await db_session.commit()
        return team.id

    team_id = _portal.call(personal_team)
    guessed = uuid.uuid5(
        uuid.UUID("ec0b38be-888e-42b5-bab5-2b0f87ac2e34"), f"{task_id}:{team_id}"
    )
    squatter = user_client.create_user()
    squatter_token = user_client.login(api_client, squatter.username, squatter.password)
    taken = api_client.post(
        "/projects",
        json={"id": str(guessed), "name": "抢先建的"},
        headers=_auth(squatter_token),
    )
    assert taken.status_code == 200, taken.text

    claimed = _claim_project(api_client, task_id, member_token)

    assert claimed != guessed
