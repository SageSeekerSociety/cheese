"""From one room, find what the rest of the project already says — only in the
rooms the caller may read, and never in another project."""

import uuid

from app.domain.block.authorship import AuthorType
from app.domain.block.models import Block, BlockKind
from app.domain.room_task.models import Task, TaskStatus
from app.domain.topic.repositories import TopicRepository
from tests.integration.conftest import post_project, session_auth_headers

OWNER = "user-1"


def _project(client) -> str:
    r = post_project(
        client, json={"name": f"上下文-{uuid.uuid4().hex[:6]}", "owner_handle": OWNER}
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _room(client, project_id: str, title: str) -> str:
    r = client.post(
        "/topics", json={"project_id": project_id, "title": title, "created_by": OWNER}
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _seed(client, fn):
    async def go():
        async with client.test_factory() as db:
            await fn(db)
            await db.commit()

    client.portal.call(go)


def _say(project: str, room: str, text: str, kind=BlockKind.message):
    async def go(db):
        db.add(
            Block(
                project_id=uuid.UUID(project),
                topic_id=uuid.UUID(room),
                kind=kind,
                author_type=AuthorType.participant,
                author=OWNER,
                content=text,
            )
        )

    return go


def _search(client, project, here, q, headers=None):
    r = client.get(
        f"/projects/{project}/context/search",
        params={"q": q, "topic": here} if here else {"q": q},
        headers=headers or {},
    )
    return r


def test_a_room_finds_what_another_room_of_the_project_said(client):
    project = _project(client)
    here = _room(client, project, "周报")
    there = _room(client, project, "预算讨论")
    _seed(client, _say(project, there, "预算定为 48 万元，其中外包 12 万"))
    _seed(client, _say(project, there, "预算按 48 万执行", kind=BlockKind.decision))

    async def task(db):
        db.add(
            Task(
                id=uuid.uuid4(),
                project_id=uuid.UUID(project),
                room_id=uuid.UUID(there),
                title="核对预算执行",
                status=TaskStatus.closed,
                conclusion="预算执行到 31.5 万",
            )
        )

    _seed(client, task)

    r = _search(client, project, here, "预算")
    assert r.status_code == 200, r.text
    hits = r.json()["data"]["hits"]
    kinds = {h["kind"] for h in hits["records"]}
    assert {"message", "decision"} <= kinds
    assert all(h["room_id"] == there for h in hits["records"])
    assert hits["records"][0]["room_title"] == "预算讨论"
    assert [t["title"] for t in hits["tasks"]] == ["核对预算执行"]
    assert any(r["room_id"] == there for r in hits["rooms"])


def test_a_private_room_and_another_project_stay_out(client):
    project = _project(client)
    here = _room(client, project, "周报")
    other = _project(client)
    elsewhere = _room(client, other, "别人的项目")
    _seed(client, _say(other, elsewhere, "机密预算 99 万"))

    async def private_room(db):
        room = await TopicRepository(db).add(
            project_id=uuid.UUID(project), title="某人的私人房间"
        )
        room.is_private = True
        await db.flush()
        db.add(
            Block(
                project_id=uuid.UUID(project),
                topic_id=room.id,
                kind=BlockKind.message,
                author_type=AuthorType.participant,
                author=OWNER,
                content="私下说的预算 77 万",
            )
        )

    _seed(client, private_room)

    data = _search(client, project, here, "预算").json()["data"]
    snippets = " ".join(h["snippet"] for h in data["hits"]["records"])
    assert "99 万" not in snippets, "another project's room was searched"
    assert "77 万" not in snippets, "a private room the caller is not in was searched"
    assert data["skipped_rooms"] >= 1


def test_a_person_outside_the_project_is_refused(client):
    project = _project(client)
    _room(client, project, "周报")
    r = _search(client, project, None, "预算", headers=session_auth_headers("user-2"))
    assert r.status_code in (401, 403)


def test_nothing_found_is_an_answer_not_an_error(client):
    project = _project(client)
    here = _room(client, project, "周报")
    data = _search(client, project, here, "根本不存在的词").json()["data"]
    assert data["total"] == 0
    assert data["searched_rooms"] >= 1
