"""改掉、删掉的记忆在面板上找得回来，而找回来的门和读写它的门是同一道。

每一次改动都留一条记录：谁、从哪条路、改之前和改之后。team 的记录项目里的人都
看得见；某个人 private 的记录只有他本人看得见，项目管理员也不例外——和读那条
记忆本身是同一条规矩。从一条记录恢复，就是把改之前那一份按正常的写入写回去：
写不动那个作用域的人恢复不了，恢复本身也留下一条记录。
"""

from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)

_NOTE = """---
name: answer-first
description: 回答先给结论
type: feedback
---

有结论就先说结论，理由跟在后面。
"""

_REWORDED = _NOTE.replace("理由跟在后面", "理由放在后面一两句")

_PRIVATE = """---
name: prefers-tabs
description: 用 tab 缩进
type: user
---

她习惯 tab。
"""


def _project(client) -> str:
    project = post_project(
        client,
        json={"name": "记忆撤销"},
        headers=session_auth_headers("alice"),
        owner="alice",
    )
    assert project.status_code == 200, project.text
    project_id = project.json()["data"]["id"]
    join_project_team(client, project_id, "bob")
    return project_id


def _put(
    client, project_id, handle, *, path, content, scope="team", owner=None, version=None
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
    response = client.put(
        "/memory/files", json=body, headers=session_auth_headers(handle)
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["file"]


def _files(client, project_id, handle, *, scope="team", owner=None) -> dict[str, str]:
    query = f"/memory/files?project_id={project_id}&scope={scope}"
    if owner is not None:
        query += f"&owner_handle={owner}"
    response = client.get(query, headers=session_auth_headers(handle))
    assert response.status_code == 200, response.text
    return {row["path"]: row["content"] for row in response.json()["data"]["data"]}


def _history(client, project_id, handle, *, scope="team", owner=None):
    query = f"/memory/files/history?project_id={project_id}&scope={scope}"
    if owner is not None:
        query += f"&owner_handle={owner}"
    return client.get(query, headers=session_auth_headers(handle))


def _changes(client, project_id, handle, **kw) -> list[dict]:
    response = _history(client, project_id, handle, **kw)
    assert response.status_code == 200, response.text
    return response.json()["data"]["data"]


def _restore(client, project_id: str, change_id: str, handle: str):
    return client.post(
        f"/memory/files/projects/{project_id}/history/{change_id}/restore",
        headers=session_auth_headers(handle),
    )


def test_each_panel_edit_and_delete_is_listed_with_who_made_it(client):
    project_id = _project(client)
    first = _put(client, project_id, "alice", path="answer-first.md", content=_NOTE)
    _put(
        client,
        project_id,
        "alice",
        path="answer-first.md",
        content=_REWORDED,
        version=first["version"],
    )
    deleted = client.post(
        "/memory/files/delete",
        json={"project_id": project_id, "scope": "team", "path": "answer-first.md"},
        headers=session_auth_headers("alice"),
    )
    assert deleted.status_code == 200, deleted.text

    # Everyone in the project reads the shared memory's history, newest first.
    changes = _changes(client, project_id, "bob")
    assert [(c["path"], c["writer"], c["changed_by"]) for c in changes] == [
        ("answer-first.md", "panel", "alice")
    ] * 3
    assert [(c["created"], c["deleted"]) for c in changes] == [
        (False, True),
        (False, False),
        (True, False),
    ]
    assert "+有结论就先说结论，理由放在后面一两句。" in changes[1]["diff"]


def test_someone_elses_private_history_is_theirs_alone(client):
    project_id = _project(client)
    _put(
        client,
        project_id,
        "alice",
        scope="private",
        owner="alice",
        path="prefers-tabs.md",
        content=_PRIVATE,
    )
    join_project_team(client, project_id, "carol", admin=True)

    own = _changes(client, project_id, "alice", scope="private", owner="alice")
    assert [c["path"] for c in own] == ["prefers-tabs.md"]
    # A teammate and a project admin are both refused, with the same code the
    # memory itself answers them with.
    for other in ("bob", "carol"):
        denied = _history(client, project_id, other, scope="private", owner="alice")
        assert denied.status_code == 403, denied.text
        # And none of it shows up in the shared history either.
        assert _changes(client, project_id, other) == []
    # Nor can they restore from it.
    assert _restore(client, project_id, own[0]["id"], "bob").status_code == 403


def test_a_memory_deleted_on_the_panel_comes_back_from_its_history(client):
    project_id = _project(client)
    _put(client, project_id, "alice", path="answer-first.md", content=_NOTE)
    _put(
        client,
        project_id,
        "alice",
        path="MEMORY.md",
        content="- [answer-first](answer-first.md) — 回答先给结论\n",
    )
    listed = client.get(
        f"/memory?project_id={project_id}", headers=session_auth_headers("alice")
    )
    assert listed.status_code == 200, listed.text
    (entry,) = listed.json()["data"]["data"]
    # The panel's delete takes the file and its index line together.
    gone = client.delete(
        f"/memory/{entry['id']}", headers=session_auth_headers("alice")
    )
    assert gone.status_code == 200, gone.text
    assert "answer-first.md" not in _files(client, project_id, "alice")

    deletion = next(
        c
        for c in _changes(client, project_id, "alice")
        if c["path"] == "answer-first.md" and c["deleted"]
    )
    restored = _restore(client, project_id, deletion["id"], "alice")
    assert restored.status_code == 200, restored.text

    files = _files(client, project_id, "alice")
    assert files["answer-first.md"] == _NOTE
    # Back where the next turn reads it: its index points at it again.
    assert "](answer-first.md)" in files["MEMORY.md"]
    # The restore is itself a change, so it can be undone in turn.
    latest = _changes(client, project_id, "alice")
    assert ("answer-first.md", "restore", "alice", True) in {
        (c["path"], c["writer"], c["changed_by"], c["created"]) for c in latest
    }


def test_a_change_that_created_a_memory_has_nothing_to_restore(client):
    project_id = _project(client)
    _put(client, project_id, "alice", path="answer-first.md", content=_NOTE)
    (creation,) = _changes(client, project_id, "alice")

    refused = _restore(client, project_id, creation["id"], "alice")
    assert refused.status_code == 422, refused.text
    assert _files(client, project_id, "alice")["answer-first.md"] == _NOTE


def _every_change(client, project_id, handle) -> list[dict]:
    """Every retained change, following the list a page at a time."""
    params = {"project_id": project_id, "scope": "team"}
    rows: list[dict] = []
    cursor = None
    while True:
        response = client.get(
            "/memory/files/history",
            params=params if cursor is None else {**params, "before": cursor},
            headers=session_auth_headers(handle),
        )
        assert response.status_code == 200, response.text
        body = response.json()["data"]
        rows += body["data"]
        cursor = body.get("next")
        if cursor is None:
            return rows


def test_a_deletion_stays_reachable_however_many_changes_follow_it(client):
    project_id = _project(client)
    _put(client, project_id, "alice", path="answer-first.md", content=_NOTE)
    deleted = client.post(
        "/memory/files/delete",
        json={"project_id": project_id, "scope": "team", "path": "answer-first.md"},
        headers=session_auth_headers("alice"),
    )
    assert deleted.status_code == 200, deleted.text
    for number in range(101):
        _put(
            client,
            project_id,
            "alice",
            path=f"later-{number}.md",
            content=_NOTE.replace("answer-first", f"later-{number}"),
        )

    # Seconds old and well inside the retention window: the panel can still
    # reach it, and restore it.
    changes = _every_change(client, project_id, "alice")
    assert len(changes) == 103
    assert len({c["id"] for c in changes}) == 103
    deletion = next(
        c for c in changes if c["path"] == "answer-first.md" and c["deleted"]
    )
    restored = _restore(client, project_id, deletion["id"], "alice")
    assert restored.status_code == 200, restored.text
    assert _files(client, project_id, "alice")["answer-first.md"] == _NOTE


def test_a_deleted_memory_comes_back_with_the_index_line_it_had(client):
    project_id = _project(client)
    # A valid name near the path limit, indexed under a short title: the line
    # it had fits the index's line budget, one rebuilt from its name would not.
    slug = "n" * 80
    path = f"{slug}.md"
    note = _NOTE.replace("answer-first", slug)
    line = f"- [回答先给结论]({path}) — 回答先给结论"
    _put(client, project_id, "alice", path=path, content=note)
    _put(client, project_id, "alice", path="MEMORY.md", content=f"{line}\n")
    listed = client.get(
        f"/memory?project_id={project_id}", headers=session_auth_headers("alice")
    )
    assert listed.status_code == 200, listed.text
    (entry,) = listed.json()["data"]["data"]
    gone = client.delete(
        f"/memory/{entry['id']}", headers=session_auth_headers("alice")
    )
    assert gone.status_code == 200, gone.text

    deletion = next(
        c
        for c in _changes(client, project_id, "alice")
        if c["path"] == path and c["deleted"]
    )
    restored = _restore(client, project_id, deletion["id"], "alice")
    assert restored.status_code == 200, restored.text
    files = _files(client, project_id, "alice")
    assert files[path] == note
    # The line it had, word for word, not one rebuilt from the file.
    assert [ln for ln in files["MEMORY.md"].splitlines() if ln] == [line]
