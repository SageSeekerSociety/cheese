"""资料库和项目详情里「挂着哪道题目、项目的申请过没过」一个请求只问一次。

读资料库的每一步都要知道项目底下有没有 `题目/`（`mount.mounted`）：列一层要问两遍，
取下载地址要问两遍，而判断 `题目/` 给不给看的那一步（`task_folder_shown`）刚把项目
行和题目行取出来。项目详情要问三遍项目的那一份申请（截止、阶段、`题目/` 在不在），
题目行也各取一次。答案在一个请求里不变，所以各问一次；不来自题目的项目连一次都不必
问，项目行已经说了。
"""

import re
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import event

from tests.integration.conftest import UserCreator, post_project, session_auth_headers
from tests.integration.test_task_files import (
    _add,
    _auth,
    _create_task,
    _member_of,
    _new_board,
    upload_root,  # noqa: F401  (fixture)
)
from tests.integration.test_task_materials_follow_the_claim import (
    _claim_as_user,
    _decide,
)
from tests.support.counting_session import counting_sql

_MOUNTED = re.compile(
    r"FROM projects JOIN task ON task\.id = projects\.external_task_id"
)
_CLAIM = re.compile(r"FROM task_membership WHERE task_membership\.task_id = ")
_TASK_ROW = re.compile(r"FROM task WHERE task\.id = ")
_LIBRARY_ROW = re.compile(r"FROM library_files WHERE .*library_files\.name = ")


def _count(statements: list[str], pattern: re.Pattern[str]) -> int:
    return sum(1 for s in statements if pattern.search(s))


def _fresh_request(api_client: TestClient, db_session, url: str, token: str):
    """One GET, its statements, and its response — on a session holding nothing,
    as a request in production starts (``api_client`` shares the test's session,
    which would otherwise still hold the rows the setup loaded)."""
    db_session.expunge_all()
    seen: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    engine = db_session.bind.sync_engine
    event.listen(engine, "after_cursor_execute", _record)
    try:
        response = api_client.get(url, headers=_auth(token))
    finally:
        event.remove(engine, "after_cursor_execute", _record)
    assert response.status_code == 200, response.text
    return seen, response


def test_a_task_projects_library_and_detail_ask_the_mount_and_claim_once(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,  # noqa: F811
    db_session,
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="挂载只问一次", startMode="APPLY")
    _add(api_client, task_id, board["creator_token"], "数据.csv", b"a,b\n")
    _, token = _member_of(user_client, api_client, board)
    participant, _ = _claim_as_user(api_client, task_id, token)
    _decide(api_client, board, task_id, participant, "APPROVED")
    _, project_id = _claim_as_user(api_client, task_id, token)

    listed, r = _fresh_request(
        api_client, db_session, f"/projects/{project_id}/library", token
    )
    assert "题目" in [row["path"] for row in r.json()["data"]["data"]]
    # task_folder_shown already read the project and its task: nothing to re-ask.
    assert _count(listed, _MOUNTED) == 0, listed
    assert _count(listed, _CLAIM) == 1, listed

    inside, r = _fresh_request(
        api_client, db_session, f"/projects/{project_id}/library?dir=题目", token
    )
    assert "题目/数据.csv" in [row["path"] for row in r.json()["data"]["data"]]
    assert _count(inside, _MOUNTED) == 0, inside

    link, r = _fresh_request(
        api_client,
        db_session,
        f"/projects/{project_id}/library/link?path=题目/数据.csv",
        token,
    )
    assert r.json()["data"]["bytes"] == 4
    assert _count(link, _MOUNTED) == 0, link
    assert _count(link, _LIBRARY_ROW) == 1, link

    detail, r = _fresh_request(api_client, db_session, f"/projects/{project_id}", token)
    assert r.json()["data"]["task"]["id"] == task_id
    assert _count(detail, _CLAIM) == 1, detail
    # The 壳 read the task row for the payload; the claim's steps reuse it.
    assert _count(detail, _TASK_ROW) == 0, detail


def test_a_project_not_from_a_task_reads_its_library_without_asking_the_mount(client):
    headers = session_auth_headers("alice")
    project_id = post_project(client, json={"name": "No task"}, owner="alice").json()[
        "data"
    ]["id"]
    for url in (
        f"/projects/{project_id}/library",
        f"/projects/{project_id}/library?flat=true",
        f"/projects/{project_id}/library/folders",
    ):
        with counting_sql(client) as statements:
            r = client.get(url, headers=headers)
        assert r.status_code == 200, r.text
        assert _count(statements, _MOUNTED) == 0, (url, statements)
