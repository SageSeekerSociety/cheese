"""提交记下「这一版要评测的代码」（方案 §提交、§数据结构）。

学生点提交时，平台把他项目**默认分支现在指着的那个 commit** 记在这一版提交上
（``TaskSubmission.code_sha``）。提交页那一块「将要评测的代码」读的是同一个数
（``GET /tasks/{id}/participants/{pid}/code-version``）；提交时若把那个版本号一并送
来（``codeSha``），默认分支已经往前走了就拒掉（``submissionCodeMoved``）。

仓库是测试的本地 git 仓库（``stub_project_forge``）：`forge.branch_commit` 读的是
``/branches/{branch}``，夹具里那一条答的就是本地仓库的 ``main``。
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator
from tests.integration.test_task_files import (
    _auth,
    _create_task,
    _member_of,
    _new_board,
    upload_root,  # noqa: F401 — 建题要的那个夹具
)
from tests.support import git_store


def _start(api_client: TestClient, task_id: int, token: str) -> dict:
    """开始做这道题：直接开始的题，这一下就通过了、开出了项目。"""
    resp = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _commit(project_id: str, name: str, text: str) -> str:
    """在项目的本地仓库 main 上再落一个提交，返回它的 sha。"""
    repo = git_store.ensure_repo(uuid.UUID(project_id))
    (repo / name).write_text(text)
    git_store.git(repo, "add", name)
    git_store.git(repo, "commit", "-qm", f"add {name}")
    return git_store.head(uuid.UUID(project_id))


def _code_version(api_client: TestClient, task_id: int, participant: int, token: str):
    return api_client.get(
        f"/tasks/{task_id}/participants/{participant}/code-version",
        headers=_auth(token),
    )


def _submit(
    api_client: TestClient,
    task_id: int,
    participant: int,
    token: str,
    *,
    code_sha: str | None = None,
):
    params = {} if code_sha is None else {"codeSha": code_sha}
    return api_client.post(
        f"/tasks/{task_id}/participants/{participant}/submissions",
        params=params,
        json=[{"text": "我的作业"}],
        headers=_auth(token),
    )


def test_the_code_version_reads_the_default_branch_head(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="将要评测的代码")
    _, token = _member_of(user_client, api_client, board)
    started = _start(api_client, task_id, token)
    participant = started["participant"]["id"]
    project_id = started["project"]["id"]

    head = _commit(project_id, "main.py", "print(1)\n")
    resp = _code_version(api_client, task_id, participant, token)
    assert resp.status_code == 200, resp.text
    version = resp.json()["data"]["codeVersion"]
    assert version["sha"] == head
    assert version["branch"] == "main"
    assert version["subject"] == "add main.py"


def test_a_submission_records_the_code_version(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="提交记代码版本")
    _, token = _member_of(user_client, api_client, board)
    started = _start(api_client, task_id, token)
    participant = started["participant"]["id"]
    project_id = started["project"]["id"]

    head = _commit(project_id, "main.py", "print(1)\n")
    resp = _submit(api_client, task_id, participant, token)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["submission"]["codeSha"] == head


def test_a_moved_branch_refuses_the_submission(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="分支动了就拒")
    _, token = _member_of(user_client, api_client, board)
    started = _start(api_client, task_id, token)
    participant = started["participant"]["id"]
    project_id = started["project"]["id"]

    stale = _commit(project_id, "main.py", "print(1)\n")
    # 页面开着的时候分支又往前走了。
    _commit(project_id, "other.py", "print(2)\n")

    resp = _submit(api_client, task_id, participant, token, code_sha=stale)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["i18n"]["key"] == "submissionCodeMoved"

    # 拿着现在这一版提交就过了。
    fresh = _code_version(api_client, task_id, participant, token).json()["data"][
        "codeVersion"
    ]["sha"]
    resp = _submit(api_client, task_id, participant, token, code_sha=fresh)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["submission"]["codeSha"] == fresh


def test_a_project_without_a_repository_leaves_the_version_empty(
    api_client: TestClient, user_client: UserCreator
):
    """没有项目（领取还没开始做）时读不到版本，提交照样成，``codeSha`` 留空。"""
    board = _new_board(user_client, api_client)
    # 申请制：加入是待批的，批了也还不开项目 —— 没有项目就没有仓库可读。
    task_id = _create_task(api_client, board, name="没有仓库的题", startMode="APPLY")
    _, token = _member_of(user_client, api_client, board)
    resp = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    participant = data["participant"]["id"]
    assert data["project"] is None

    resp = _code_version(api_client, task_id, participant, token)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["codeVersion"] is None
