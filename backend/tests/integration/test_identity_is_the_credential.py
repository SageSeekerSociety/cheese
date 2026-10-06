"""Who is acting comes from the credential, never from the request body.

`POST /projects` used to take `owner_handle` from the body ahead of the
signed-in caller, so any member could create a project that belonged to
somebody else. The same family of fields — `created_by`, `author`, `by` — sat on
eight more routes and named the actor whenever no credential did. Each test
here has member A send B's name in the body and checks that the result is A's,
or that the request is refused.
"""

import asyncio
import uuid

from sqlalchemy import select

from app.domain.room_task.models import TaskTitle
from tests.conftest import seed_user
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)
from tests.support.living_doc import document_of

A = "alice"
B = "bob"


def _as(handle: str) -> dict[str, str]:
    return session_auth_headers(handle)


def _project(client, owner: str = A) -> dict:
    r = post_project(client, json={"name": f"{owner}'s"}, owner=owner)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _visible_projects(client, handle: str) -> list[str]:
    r = client.get("/projects", headers=_as(handle))
    assert r.status_code == 200, r.text
    return [p["id"] for p in r.json()["data"]["data"]]


# --- POST /projects ----------------------------------------------------------


def test_a_project_belongs_to_whoever_created_it_whatever_the_body_says(client):
    seed_user(client, A)
    seed_user(client, B)

    r = client.post(
        "/projects", json={"name": "mine", "owner_handle": B}, headers=_as(A)
    )

    assert r.status_code == 200, r.text
    made = r.json()["data"]
    assert made["owner_handle"] == A
    assert made["id"] in _visible_projects(client, A)
    assert made["id"] not in _visible_projects(client, B)


def test_a_body_owner_does_not_stand_in_for_a_credential(client):
    """The dev credential the test client carries opens the write surface but
    names nobody, so naming an owner in the body is all that is left — and it
    is not enough."""
    seed_user(client, B)

    r = client.post("/projects", json={"name": "planted", "owner_handle": B})

    assert r.status_code == 401, r.text
    assert _visible_projects(client, B) == []


# --- POST /topics ------------------------------------------------------------


def _roster_owner(client, topic_id: str, viewer: str) -> list[str]:
    r = client.get(f"/topics/{topic_id}/members", headers=_as(viewer))
    assert r.status_code == 200, r.text
    return [
        m["member_handle"] for m in r.json()["data"]["data"] if m["role"] == "owner"
    ]


def test_a_room_is_owned_by_its_creator_not_by_the_body(client):
    project = _project(client, A)
    join_project_team(client, project["id"], B)

    r = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "房间", "created_by": B},
        headers=_as(A),
    )

    assert r.status_code == 200, r.text
    assert _roster_owner(client, r.json()["data"]["id"], A) == [A]


# --- PUT /documents/{id} ------------------------------------------------------


def test_a_doc_edit_is_signed_by_the_editor(client):
    project = _project(client, A)
    room = project["root_topic_id"]

    doc = document_of(client, room, headers=_as(A))
    r = client.put(
        f"/documents/{doc}",
        json={"content": "# 计划\n\n先做数据", "expected_version": 0, "author": B},
        headers=_as(A),
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["author"] == A


# --- POST /documents/{id}/comments -------------------------------------------


def test_a_comment_is_signed_by_its_writer(client):
    project = _project(client, A)
    room = project["root_topic_id"]

    doc = document_of(client, room, headers=_as(A))
    r = client.post(
        f"/documents/{doc}/comments",
        json={"content": "这里要再想想", "author": B},
        headers=_as(A),
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["author"] == A


def test_the_dev_credential_alone_cannot_sign_a_comment_as_someone(client):
    project = _project(client, A)
    room = project["root_topic_id"]

    doc = document_of(client, room, headers=_as(A))
    r = client.post(f"/documents/{doc}/comments", json={"content": "冒名", "author": B})

    assert r.status_code == 200, r.text
    assert r.json()["data"]["author"] != B


# --- POST /topics/{id}/tasks + /tasks/{task}/messages --------------------------


def test_a_task_and_a_message_in_it_belong_to_the_caller(client):
    project = _project(client, A)
    room = project["root_topic_id"]

    made = client.post(
        f"/topics/{room}/tasks",
        json={"title": "清洗数据", "owner_handle": B, "created_by": B},
        headers=_as(A),
    )
    assert made.status_code == 200, made.text
    task = made.json()["data"]
    assert task["owner_handle"] == A

    said = client.post(
        f"/topics/{task['id']}/messages",
        json={"request_id": str(uuid.uuid4()), "content": "先跑小样本", "author": B},
        headers=_as(A),
    )
    assert said.status_code == 200, said.text
    blocks = client.get(f"/topics/{task['id']}/blocks", headers=_as(A)).json()["data"][
        "data"
    ]
    mine = [b for b in blocks if b.get("content") == "先跑小样本"]
    assert [b["author"] for b in mine] == [A]


# --- POST /topics/{id}/title -------------------------------------------------


def test_a_rename_is_recorded_as_the_renamer(client):
    project = _project(client, A)
    room = project["root_topic_id"]
    made = client.post(f"/topics/{room}/tasks", json={}, headers=_as(A))
    assert made.status_code == 200, made.text
    task = made.json()["data"]["id"]

    r = client.post(
        f"/topics/{task}/title", json={"title": "新名字", "by": B}, headers=_as(A)
    )
    assert r.status_code == 200, r.text

    async def renamers() -> list[str | None]:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            rows = await session.execute(
                select(TaskTitle.by).where(
                    TaskTitle.task_id == uuid.UUID(task), TaskTitle.title == "新名字"
                )
            )
            return list(rows.scalars())

    assert asyncio.run(renamers()) == [A]


# --- routes where the body name could only borrow access -----------------------


def test_an_outsider_cannot_summon_by_naming_a_member(client):
    project = _project(client, B)
    seed_user(client, A)

    r = client.post(
        f"/topics/{project['root_topic_id']}/summon",
        json={"author": B},
        headers=_as(A),
    )

    assert r.status_code == 403, r.text


def test_an_outsider_cannot_clone_a_room_by_naming_its_member(client):
    """Cloning reads the source room's conversation, so the caller must be
    allowed into it. Naming someone who is does not make the caller them."""
    mine = _project(client, A)
    theirs = _project(client, B)

    r = client.post(
        f"/topics/{mine['root_topic_id']}/clone-from",
        json={"source_topic_id": theirs["root_topic_id"], "by": B},
        headers=_as(A),
    )

    assert r.status_code == 403, r.text


# --- POST /documents/{id}/edits ----------------------------------------------


def test_a_passage_edit_cannot_carry_another_author(client):
    """This body is strict, so a name in it is refused outright."""
    project = _project(client, A)
    room = project["root_topic_id"]
    doc = document_of(client, room, headers=_as(A))
    client.put(
        f"/documents/{doc}",
        json={"content": "# 计划\n\n先做数据", "expected_version": 0},
        headers=_as(A),
    )

    r = client.post(
        f"/documents/{doc}/edits",
        json={"edits": [{"old": "先做数据", "new": "先做模型"}], "author": B},
        headers=_as(A),
    )

    assert r.status_code == 400, r.text
