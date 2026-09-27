"""记忆的两个作用域各守各的门，写入带版本号。

team 是项目里所有人共看的一份；private 是「这个人 × 这个项目」——本人和项目管理员
看得见，别人问起答 403 而不是 404（private 的存在本身不是秘密，里面的内容才是）。
写入对号入座的那个版本号对不上就是 409 并把当前那一版还回去：冲突是拒绝，不是把两
段散文悄悄合在一起。
"""

from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)

_TEAM_FILE = """---
name: answer-first
description: 回答先给结论
type: feedback
---

有结论就先说结论，理由跟在后面。
"""


def _project(client) -> str:
    alice = session_auth_headers("alice")
    project = post_project(
        client, json={"name": "记忆", "owner_handle": "alice"}, headers=alice
    )
    assert project.status_code == 200, project.text
    project_id = project.json()["data"]["id"]
    join_project_team(client, project_id, "bob")
    return project_id


def _put(
    client,
    project_id: str,
    handle: str,
    *,
    scope: str = "team",
    owner: str | None = None,
    path: str,
    content: str,
    version: int | None = None,
):
    body: dict = {
        "project_id": project_id,
        "scope": scope,
        "path": path,
        "content": content,
    }
    if owner is not None:
        body["owner_handle"] = owner
    if version is not None:
        body["version"] = version
    return client.put("/memory/files", json=body, headers=session_auth_headers(handle))


def _get(client, project_id: str, handle: str, *, scope: str = "team", owner=None):
    query = f"/memory/files?project_id={project_id}&scope={scope}"
    if owner is not None:
        query += f"&owner_handle={owner}"
    return client.get(query, headers=session_auth_headers(handle))


def test_a_team_memory_is_written_by_one_member_and_read_by_another(client):
    """team 是共看共写的一份：谁写的都一样读得到，版本号跟着写入往前走。"""
    project_id = _project(client)

    written = _put(
        client,
        project_id,
        "alice",
        path="answer-first.md",
        content=_TEAM_FILE,
    )
    assert written.status_code == 200, written.text
    assert written.json()["data"]["file"]["version"] == 1

    seen = _get(client, project_id, "bob")
    assert seen.status_code == 200, seen.text
    files = {row["path"]: row for row in seen.json()["data"]["data"]}
    assert files["answer-first.md"]["content"] == _TEAM_FILE

    again = _put(
        client,
        project_id,
        "bob",
        path="answer-first.md",
        content=_TEAM_FILE + "\n改了主意。\n",
        version=1,
    )
    assert again.status_code == 200, again.text
    assert again.json()["data"]["file"]["version"] == 2


def test_a_members_private_memory_is_not_everyones(client):
    """私聊里学到的偏好不该出现在队友眼前，也不该说「这里没有」。"""
    project_id = _project(client)
    mine = _put(
        client,
        project_id,
        "alice",
        scope="private",
        owner="alice",
        path="prefers-tabs.md",
        content=(
            "---\nname: prefers-tabs\ndescription: 用 tab 缩进\ntype: user\n---\n\n"
            "她习惯 tab。\n"
        ),
    )
    assert mine.status_code == 200, mine.text

    own = _get(client, project_id, "alice", scope="private", owner="alice")
    assert own.status_code == 200, own.text
    denied = _get(client, project_id, "bob", scope="private", owner="alice")
    assert denied.status_code == 403, denied.text
    # 写别人的也是同一道门。
    assert (
        _put(
            client,
            project_id,
            "bob",
            scope="private",
            owner="alice",
            path="prefers-tabs.md",
            content="不是他写的",
        ).status_code
        == 403
    )
    # 而 team 那一份里没有它。
    listed = _get(client, project_id, "bob")
    assert [row["path"] for row in listed.json()["data"]["data"]] == []


def test_a_project_admin_may_read_a_members_private_memory(client):
    """项目管理员要能处理「这个人为什么被这么对待」，所以看得见，但仍是别人的。"""
    project_id = _project(client)
    _put(
        client,
        project_id,
        "alice",
        scope="private",
        owner="alice",
        path="prefers-tabs.md",
        content=(
            "---\nname: prefers-tabs\ndescription: 用 tab\ntype: user\n---\n\n"
            "她习惯 tab。\n"
        ),
    )
    join_project_team(client, project_id, "carol", admin=True)

    assert (
        _get(client, project_id, "carol", scope="private", owner="alice").status_code
        == 200
    )


def test_a_stale_version_is_refused_with_the_current_one(client):
    project_id = _project(client)
    _put(client, project_id, "alice", path="answer-first.md", content=_TEAM_FILE)

    stale = _put(
        client,
        project_id,
        "alice",
        path="answer-first.md",
        content=_TEAM_FILE + "\n半路改的。\n",
        version=7,
    )
    assert stale.status_code == 409, stale.text
    # 带着两个版本号回来：重读这件事要动手，动手的人得知道自己在重读第几版。
    assert stale.json()["error"]["data"] == {
        "path": "answer-first.md",
        "expected": 7,
        "current": 1,
    }


def test_a_new_memory_under_an_existing_name_is_a_conflict(client):
    """「先查重，再新建」是写记忆的规矩，而一个重名的空文件正是它最容易被绕过的地方。"""
    project_id = _project(client)
    _put(client, project_id, "alice", path="answer-first.md", content=_TEAM_FILE)

    assert (
        _put(
            client, project_id, "alice", path="answer-first.md", content=_TEAM_FILE
        ).status_code
        == 409
    )


def test_a_path_may_not_walk_out_of_its_directory(client):
    """会话里铺下来的是真文件，回写按路径对号入座——所以路径先得过这一关。

    422 是这个仓库里「你写的东西不成形」的那个码（`ValidationError`），别的写
    接口也一样；要紧的是它不能读成服务器内部错误。
    """
    project_id = _project(client)

    walked = _put(
        client,
        project_id,
        "alice",
        path="../../etc/passwd.md",
        content="不是记忆",
    )
    assert walked.status_code == 422, walked.text


def test_a_bad_scope_is_a_422_not_a_500(client):
    project_id = _project(client)
    bad = _put(
        client,
        project_id,
        "alice",
        scope="team; private",
        path="a.md",
        content="x",
    )
    assert bad.status_code == 422, bad.text
