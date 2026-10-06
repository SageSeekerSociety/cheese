"""Documents of the project's own: the library's documents.

Members make, list, rename and delete them; a task's living document and the
project's overview are not listed, nor renamed or deleted on their own, though a
member may keep a copy of one. When 芝士 makes or changes one while working in
a room, that room gets a line with the document on it; a person's change does
not.
"""

from app.core.sandbox_auth import mint_scoped_token
from app.domain.living_doc import collab
from tests.integration.conftest import (
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
)
from tests.support.living_doc import document_of, overview_of

OWNER = session_auth_headers("owner")
OUTSIDER = session_auth_headers("outsider")


def _room(client) -> tuple[str, str]:
    """A room, in a project of its own, of owner's; returns (room, project)."""
    project = post_project(client, json={"name": "P"}).json()["data"]["id"]
    room = client.post(
        "/topics", json={"project_id": project, "title": "话题"}, headers=OWNER
    ).json()["data"]["id"]
    return room, project


def _task_doc(client, room) -> str:
    """The living document of a new task of owner's in the room."""
    task = open_task(client, room, owner="owner", start=False)["id"]
    return document_of(client, task, headers=OWNER)


def _setup(client, rooms: int = 1) -> list[tuple[str, str, dict[str, str]]]:
    """Rooms in projects of their own, each with its project and the credential
    a session of its 芝士 runs with; then the client speaks as nobody until a
    test says who."""
    made = []
    for _ in range(rooms):
        room, project = _room(client)
        token = mint_scoped_token(
            project_id=project,
            topic_id=str(room),
            agent_handle=room_agent_seat(client, room),
            access_scope="project",
        )
        made.append((room, project, {"X-Cheese-Token": token}))
    client.headers.pop("X-Cheese-Token", None)
    return made


def _create(client, project, headers=OWNER, **body) -> dict:
    made = client.post(f"/projects/{project}/documents", json=body, headers=headers)
    assert made.status_code == 200, made.text
    return made.json()["data"]


def _listed(client, project) -> list[dict]:
    listed = client.get(f"/projects/{project}/documents", headers=OWNER)
    assert listed.status_code == 200, listed.text
    return listed.json()["data"]["data"]


def _content(client, doc_id) -> str:
    data = client.get(f"/documents/{doc_id}", headers=OWNER).json()["data"]
    return data["content"] if data else ""


def _room_lines(client, room) -> list[dict]:
    rows = client.get(f"/topics/{room}/blocks", headers=OWNER).json()["data"]["data"]
    return [b for b in rows if (b.get("meta") or {}).get("document")]


def test_members_make_list_rename_and_delete_the_projects_documents(client):
    [(room, project, _)] = _setup(client)
    made = _create(client, project, title="竞品定价对比")
    # Listed, named and owned before anything is written in it.
    assert [(d["id"], d["title"], d["author"]) for d in _listed(client, project)] == [
        (made["id"], "竞品定价对比", "owner")
    ]
    # A task's document is the task's, and the overview the project's: neither
    # is in the library.
    listed = [d["id"] for d in _listed(client, project)]
    assert _task_doc(client, room) not in listed
    assert overview_of(client, project, headers=OWNER) not in listed

    renamed = client.patch(
        f"/documents/{made['id']}", json={"title": "定价对比"}, headers=OWNER
    )
    assert renamed.status_code == 200, renamed.text
    assert _listed(client, project)[0]["title"] == "定价对比"

    assert client.delete(f"/documents/{made['id']}", headers=OWNER).status_code == 200
    assert _listed(client, project) == []
    assert client.get(f"/documents/{made['id']}", headers=OWNER).status_code == 404


def test_nobody_outside_the_project_reaches_its_documents(client):
    [(_, project, _)] = _setup(client)
    made = _create(client, project, title="内部")
    assert (
        client.get(f"/projects/{project}/documents", headers=OUTSIDER).status_code
        == 403
    )
    refused = client.post(
        f"/projects/{project}/documents", json={"title": "x"}, headers=OUTSIDER
    )
    assert refused.status_code == 403
    path = f"/documents/{made['id']}"
    assert client.patch(path, json={"title": "x"}, headers=OUTSIDER).status_code == 403
    assert client.delete(path, headers=OUTSIDER).status_code == 403
    assert _listed(client, project)[0]["title"] == "内部"


def test_the_overview_and_a_tasks_document_are_not_renamed_or_deleted_alone(client):
    [(room, project, _)] = _setup(client)
    for what, doc in (
        ("the overview", overview_of(client, project, headers=OWNER)),
        ("a task's document", _task_doc(client, room)),
    ):
        path = f"/documents/{doc}"
        renamed = client.patch(path, json={"title": "x"}, headers=OWNER)
        assert renamed.status_code == 403, what
        assert client.delete(path, headers=OWNER).status_code == 403, what
        assert client.get(f"{path}/about", headers=OWNER).status_code == 200


def test_an_archived_project_takes_no_new_document(client):
    [(_, project, _)] = _setup(client)
    archived = client.post(f"/projects/{project}/archive", headers=OWNER)
    assert archived.status_code == 200, archived.text
    refused = client.post(
        f"/projects/{project}/documents", json={"title": "x"}, headers=OWNER
    )
    assert refused.status_code >= 400
    assert _listed(client, project) == []


def test_the_library_search_finds_its_documents(client):
    [(_, project, _)] = _setup(client)
    by_title = _create(client, project, title="定价方案")
    by_text = _create(client, project, title="周会", content="下周讨论定价和退款")
    _create(client, project, title="无关", content="首页草图")

    found = client.get(
        f"/projects/{project}/documents/search", params={"q": "定价"}, headers=OWNER
    )
    assert found.status_code == 200, found.text
    data = found.json()["data"]
    assert {d["id"] for d in data["library"]} == {by_title["id"], by_text["id"]}

    refused = client.get(
        f"/projects/{project}/documents/search", params={"q": "定价"}, headers=OUTSIDER
    )
    assert refused.status_code == 403


def test_a_kept_copy_and_its_original_change_apart(client):
    [(room, project, _)] = _setup(client)
    task_doc = _task_doc(client, room)
    client.put(
        f"/documents/{task_doc}",
        json={"content": "原来的结论", "expected_version": 0},
        headers=OWNER,
    )
    copy = _create(client, project, copy_of=task_doc)
    # It says what the task's document said.
    assert _content(client, copy["id"]) == "原来的结论"

    edited = client.post(
        f"/documents/{copy['id']}/edits",
        json={"edits": [{"old": "原来的结论", "new": "副本的结论"}], "mode": "direct"},
        headers=OWNER,
    )
    assert edited.status_code == 200, edited.text
    assert _content(client, copy["id"]) == "副本的结论"
    assert _content(client, task_doc) == "原来的结论"


def test_a_copy_of_another_projects_document_is_refused(client):
    [(room, _, _), (_, other, _)] = _setup(client, rooms=2)
    task_doc = _task_doc(client, room)
    refused = client.post(
        f"/projects/{other}/documents", json={"copy_of": task_doc}, headers=OWNER
    )
    assert refused.status_code == 404
    assert _listed(client, other) == []


def test_cheese_making_and_changing_a_document_tells_the_room_it_works_in(client):
    [(room, project, agent)] = _setup(client)
    made = _create(client, project, headers=agent, title="竞品定价对比", content="三家")
    assert _content(client, made["id"]) == "三家"
    [line] = _room_lines(client, room)
    assert line["meta"]["document"] == {"id": made["id"], "title": "竞品定价对比"}
    assert line["meta"]["doc_created"] is True

    for old, new in (("三家", "三家都有年付"), ("年付", "年付折扣")):
        edited = client.post(
            f"/documents/{made['id']}/edits",
            json={"edits": [{"old": old, "new": new}], "mode": "direct"},
            headers=agent,
        )
        assert edited.status_code == 200, edited.text
    assert _content(client, made["id"]) == "三家都有年付折扣"
    # A run of changes right after making it is the same line, listing them.
    [line] = _room_lines(client, room)
    assert [e["new"] for e in line["meta"]["doc_edits"]] == ["三家都有年付", "年付折扣"]


def test_a_persons_change_to_a_document_tells_no_room(client):
    [(room, project, _)] = _setup(client)
    made = _create(client, project, title="笔记", content="第一版")
    client.post(
        f"/documents/{made['id']}/edits",
        json={"edits": [{"old": "第一版", "new": "第二版"}], "mode": "direct"},
        headers=OWNER,
    )
    assert _content(client, made["id"]) == "第二版"
    assert _room_lines(client, room) == []


def test_a_deleted_document_is_not_stored_back_by_an_editor_left_open(client):
    [(_, project, _)] = _setup(client)
    made = _create(client, project, title="草稿", content="写到一半")
    name = client.get(f"/documents/{made['id']}/ticket", headers=OWNER).json()["data"][
        "document"
    ]
    assert client.delete(f"/documents/{made['id']}", headers=OWNER).status_code == 200
    assert client.collab.told[name][-1] == {"type": "state", "resource": "deleted"}
    stored = client.put(
        f"/internal/collab/documents/{name}",
        json={"state": "", "content": "删了以后又打的字", "actors": ["owner"]},
        headers={"Authorization": f"Bearer {collab._key('internal')}"},
    )
    assert stored.status_code == 404
    assert _listed(client, project) == []


def test_cheese_of_another_project_does_not_reach_these_documents(client):
    [(_, project, _), (_, _, stranger)] = _setup(client, rooms=2)
    made = _create(client, project, title="内部")
    assert (
        client.get(f"/projects/{project}/documents", headers=stranger).status_code
        == 403
    )
    assert client.get(f"/documents/{made['id']}", headers=stranger).status_code == 403
    refused = client.post(
        f"/projects/{project}/documents", json={"title": "x"}, headers=stranger
    )
    assert refused.status_code == 403


def test_cheese_taken_off_its_room_no_longer_reaches_the_documents(client):
    [(room, project, agent)] = _setup(client)
    made = _create(client, project, title="内部")
    rows = client.get(f"/topics/{room}/members", headers=OWNER).json()["data"]["data"]
    [seat] = [m["member_handle"] for m in rows if m["agent"]]
    taken_off = client.delete(f"/topics/{room}/members/{seat}", headers=OWNER)
    assert taken_off.status_code == 200, taken_off.text
    assert (
        client.get(f"/projects/{project}/documents", headers=agent).status_code == 403
    )
    assert client.get(f"/documents/{made['id']}", headers=agent).status_code == 403
    refused = client.post(
        f"/projects/{project}/documents", json={"title": "x"}, headers=agent
    )
    assert refused.status_code == 403


def test_cheese_suggesting_changes_to_a_document_tells_the_room_so(client):
    [(room, project, agent)] = _setup(client)
    made = _create(client, project, title="方案", content="原来的写法")
    proposed = client.post(
        f"/documents/{made['id']}/edits",
        json={"edits": [{"old": "原来的写法", "new": "新的写法"}], "mode": "suggest"},
        headers=agent,
    )
    assert proposed.status_code == 200, proposed.text
    assert _content(client, made["id"]) == "原来的写法"
    [line] = _room_lines(client, room)
    assert line["meta"]["doc_suggested"] is True
    assert len(line["meta"]["doc_suggestions"]) == 1


def test_a_tasks_document_is_the_tasks_not_the_librarys(client):
    """A task's living document sits in no room, like the project's own, but
    it is the task's: the library neither lists nor finds it."""
    [(room, project, _)] = _setup(client)
    task = open_task(client, room, owner="owner", reviewer="owner")
    task_doc = client.get(f"/topics/{task['id']}/document", headers=OWNER)
    assert task_doc.status_code == 200, task_doc.text
    task_doc_id = task_doc.json()["data"]["id"]
    written = client.put(
        f"/documents/{task_doc_id}",
        json={"content": "这个任务里的定价草稿", "expected_version": 0},
        headers=OWNER,
    )
    assert written.status_code == 200, written.text

    assert task_doc_id not in {d["id"] for d in _listed(client, project)}
    found = client.get(
        f"/projects/{project}/documents/search", params={"q": "定价"}, headers=OWNER
    ).json()["data"]
    assert task_doc_id not in {d["id"] for d in found["library"]}
