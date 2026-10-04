"""记忆的两个作用域各守各的门，写入带版本号。

project 是项目里所有人共看的一份；private 是「这个人 × 这个项目」——本人和项目管理员
看得见，别人问起答 403 而不是 404（private 的存在本身不是秘密，里面的内容才是）。
**读得到不等于写得动**：private 那一侧的写和删只认本人，管理员也不行。写入对号入座
的那个版本号对不上就是 409 并把当前那一版还回去：冲突是拒绝，不是把两段散文悄悄合
在一起。
"""

from app.domain.memory.files_store import MemoryFileStore
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)

_PROJECT_FILE = """---
name: answer-first
description: 回答先给结论
type: feedback
---

有结论就先说结论，理由跟在后面。
"""

_PRIVATE_FILE = """---
name: prefers-tabs
description: 用 tab 缩进
type: user
---

她习惯 tab。
"""


def _project(client) -> str:
    alice = session_auth_headers("alice")
    project = post_project(client, json={"name": "记忆"}, headers=alice, owner="alice")
    assert project.status_code == 200, project.text
    project_id = project.json()["data"]["id"]
    join_project_team(client, project_id, "bob")
    return project_id


def _put(
    client,
    project_id: str,
    handle: str,
    *,
    scope: str = "project",
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


def _delete(
    client,
    project_id: str,
    handle: str,
    *,
    scope: str = "project",
    owner: str | None = None,
    path: str,
    version: int | None = None,
):
    body: dict = {"project_id": project_id, "scope": scope, "path": path}
    if owner is not None:
        body["owner_handle"] = owner
    if version is not None:
        body["version"] = version
    return client.post(
        "/memory/files/delete", json=body, headers=session_auth_headers(handle)
    )


def _get(client, project_id: str, handle: str, *, scope: str = "project", owner=None):
    query = f"/memory/files?project_id={project_id}&scope={scope}"
    if owner is not None:
        query += f"&owner_handle={owner}"
    return client.get(query, headers=session_auth_headers(handle))


def test_a_project_memory_is_written_by_one_member_and_read_by_another(client):
    """project 是共看共写的一份：谁写的都一样读得到，版本号跟着写入往前走。"""
    project_id = _project(client)

    written = _put(
        client,
        project_id,
        "alice",
        path="answer-first.md",
        content=_PROJECT_FILE,
    )
    assert written.status_code == 200, written.text
    assert written.json()["data"]["file"]["version"] == 1

    seen = _get(client, project_id, "bob")
    assert seen.status_code == 200, seen.text
    files = {row["path"]: row for row in seen.json()["data"]["data"]}
    assert files["answer-first.md"]["content"] == _PROJECT_FILE

    again = _put(
        client,
        project_id,
        "bob",
        path="answer-first.md",
        content=_PROJECT_FILE + "\n改了主意。\n",
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
        content=_PRIVATE_FILE,
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
    # 删别人的也是同一道门。
    assert (
        _delete(
            client,
            project_id,
            "bob",
            scope="private",
            owner="alice",
            path="prefers-tabs.md",
        ).status_code
        == 403
    )
    # 而 project 那一份里没有它。
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
        content=_PRIVATE_FILE,
    )
    join_project_team(client, project_id, "carol", admin=True)

    assert (
        _get(client, project_id, "carol", scope="private", owner="alice").status_code
        == 200
    )


def test_a_project_admin_may_not_write_a_members_private_memory(client):
    """那道门是**看**，不是**改**。

    给他一支笔，这一条记忆就同时有了两个主人，而「这是谁的判断」正是 private 这
    一层唯一要保住的东西。查得到就够了。
    """
    project_id = _project(client)
    _put(
        client,
        project_id,
        "alice",
        scope="private",
        owner="alice",
        path="prefers-tabs.md",
        content=_PRIVATE_FILE,
    )
    join_project_team(client, project_id, "carol", admin=True)

    # 覆盖：版本号读对了也不行。
    overwrite = _put(
        client,
        project_id,
        "carol",
        scope="private",
        owner="alice",
        path="prefers-tabs.md",
        content=_PRIVATE_FILE,
        version=1,
    )
    assert overwrite.status_code == 403, overwrite.text
    # 新建一条挂在她名下：同一个道理，那不是他的判断。
    added = _put(
        client,
        project_id,
        "carol",
        scope="private",
        owner="alice",
        path="answered-late.md",
        content=_PRIVATE_FILE,
    )
    assert added.status_code == 403, added.text
    deleted = _delete(
        client,
        project_id,
        "carol",
        scope="private",
        owner="alice",
        path="prefers-tabs.md",
        version=1,
    )
    assert deleted.status_code == 403, deleted.text

    # 被拒的那三次一个字都没落下来：本人读到的还是原来那一版。
    theirs = _get(client, project_id, "alice", scope="private", owner="alice")
    rows = {row["path"]: row for row in theirs.json()["data"]["data"]}
    assert set(rows) == {"prefers-tabs.md"}
    assert rows["prefers-tabs.md"]["version"] == 1
    assert rows["prefers-tabs.md"]["content"] == _PRIVATE_FILE


def test_a_path_longer_than_the_column_is_a_422_not_a_500(client):
    """`memory_files.path` 是 ``String(200)``。

    比它长的一句在数据库那一侧是「服务器内部错误」，而写它的 agent 要的是「哪一条
    不对、怎么改」——照着拒绝它改得动，照着 500 它改不动。
    """
    project_id = _project(client)
    at_the_limit = "a" * 197 + ".md"
    assert len(at_the_limit) == 200

    written = _put(
        client, project_id, "alice", path=at_the_limit, content=_PROJECT_FILE
    )
    assert written.status_code == 200, written.text

    over = _put(
        client, project_id, "alice", path="a" * 198 + ".md", content=_PROJECT_FILE
    )
    assert over.status_code == 422, over.text
    assert "200" in over.json()["error"]["message"]


def test_two_writers_of_the_same_new_path_end_in_a_409_not_a_500(client, monkeypatch):
    """「先查重，再新建」之间有一道缝：两个写入方同时起手，后到的那一个会在 flush
    的时候撞上唯一约束。

    那不是故障，是这条规矩本身的结果——接住它，把当前那一版还回去，让写的人重读一
    次。这里让下一个写入方那一次「查」看不到刚写下的那一行，就是那道缝。
    """
    project_id = _project(client)
    first = _put(
        client, project_id, "alice", path="answer-first.md", content=_PROJECT_FILE
    )
    assert first.status_code == 200, first.text

    real_get = MemoryFileStore.get
    looked: list[str] = []

    async def blind_once(*args, **kwargs):
        if not looked:
            looked.append("第一次查没看到那一条")
            return None
        return await real_get(*args, **kwargs)

    monkeypatch.setattr(MemoryFileStore, "get", blind_once)

    raced = _put(
        client, project_id, "alice", path="answer-first.md", content=_PROJECT_FILE
    )
    assert raced.status_code == 409, raced.text
    assert raced.json()["error"]["data"] == {
        "path": "answer-first.md",
        "expected": None,
        "current": 1,
    }


def test_a_stale_version_is_refused_with_the_current_one(client):
    project_id = _project(client)
    _put(client, project_id, "alice", path="answer-first.md", content=_PROJECT_FILE)

    stale = _put(
        client,
        project_id,
        "alice",
        path="answer-first.md",
        content=_PROJECT_FILE + "\n半路改的。\n",
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
    _put(client, project_id, "alice", path="answer-first.md", content=_PROJECT_FILE)

    assert (
        _put(
            client, project_id, "alice", path="answer-first.md", content=_PROJECT_FILE
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
        scope="project; private",
        path="a.md",
        content="x",
    )
    assert bad.status_code == 422, bad.text


def _long_note() -> str:
    return _PROJECT_FILE.replace("有结论就先说结论，理由跟在后面。", "字" * 1001)


def test_a_memory_over_the_length_limit_is_refused_and_nothing_is_written(client):
    """超了单条上限就一个字都不写：新建的不存在，改写的还是原来那一版。"""
    project_id = _project(client)

    created = _put(
        client, project_id, "alice", path="too-long.md", content=_long_note()
    )
    assert created.status_code == 422, created.text

    kept = _put(
        client, project_id, "alice", path="answer-first.md", content=_PROJECT_FILE
    )
    assert kept.status_code == 200, kept.text
    longer = _put(
        client,
        project_id,
        "alice",
        path="answer-first.md",
        content=_long_note(),
        version=1,
    )
    assert longer.status_code == 422, longer.text

    files = {
        row["path"]: row
        for row in _get(client, project_id, "alice").json()["data"]["data"]
    }
    assert "too-long.md" not in files
    assert files["answer-first.md"]["content"] == _PROJECT_FILE
    assert files["answer-first.md"]["version"] == 1


def test_a_session_that_skips_the_check_is_still_refused_by_the_platform(client):
    """会话那一侧没拦下的超长版本（比这一版旧的会话），平台那一道照样不收。"""
    import uuid

    from app.domain.memory.files import MemoryFileScope
    from app.domain.memory.session import apply_tree, read_tree

    project_id = _project(client)
    written = _put(
        client, project_id, "alice", path="answer-first.md", content=_PROJECT_FILE
    )
    assert written.status_code == 200, written.text
    scopes = [(MemoryFileScope.project, None)]

    async def run():
        async with client.test_request_factory() as session:
            project = uuid.UUID(project_id)
            stored = await read_tree(session, project, scopes)
            change = await apply_tree(
                session,
                project,
                stored,
                {"files": {"project/answer-first.md": _long_note()}, "refused": {}},
                scopes=scopes,
                updated_by="cheese",
            )
            await session.commit()
            return change

    change = client.portal.call(run)
    assert "project/answer-first.md" in change.rejected
    files = {
        row["path"]: row
        for row in _get(client, project_id, "alice").json()["data"]["data"]
    }
    assert files["answer-first.md"]["content"] == _PROJECT_FILE
