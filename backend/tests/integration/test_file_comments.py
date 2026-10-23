"""评论挂在房间文件上：真 HTTP、真库。

说给人听的规则：
- 一份交付出来的文件上能开一条评论，指出「第 3 页这里」；回复、解决、重开和文档
  上的评论是同一套，一次写入按操作 id 幂等。
- 评论挂在文件上，不挂在文档上；同一间房里的另一份文件看不到它。
- 归档的频道收不下新评论，和它的文档一样。
- 指认说的是另一份文件时拒收。
"""

import asyncio
import uuid

from sqlalchemy import select

from app.domain.library import service as library
from app.domain.living_doc.models import DocumentComment
from app.domain.topic.models import Topic, TopicStatus
from tests.integration.conftest import post_project

PATH = "output/报告.pdf"
OTHER = "output/别的.pdf"


def _room(client) -> str:
    project = post_project(client, json={"name": "文件评论"}).json()["data"]["id"]
    return client.post(
        "/topics", json={"project_id": project, "title": "改一份报告"}
    ).json()["data"]["id"]


def _file(client, room: str, path: str = PATH) -> None:
    """把一份文件放进房间：干活的那台机器往房间目录里写的就是这件事。"""
    project = client.get(f"/topics/{room}").json()["data"]["project_id"]
    library.write_room_file(
        uuid.UUID(project), uuid.UUID(room), path, b"%PDF-1.4 test bytes"
    )


def _anchor(path: str = PATH) -> dict:
    return {
        "kind": "page-pin",
        "path": path,
        "source": "live",
        "version": "0123456789abcdef",
        "task_id": None,
        "page": 3,
        "x": 0.4,
        "y": 0.6,
    }


def _mutation(revision: int, **payload):
    return {"operation_id": str(uuid.uuid4()), "expected_revision": revision, **payload}


def _open(client, room: str, **extra):
    body = {
        "path": PATH,
        "content": "这里不对",
        "quote": "第 3 页的一句",
        "anchor": _anchor(),
    }
    body.update(extra)
    return client.post(f"/topics/{room}/files/comments", json=body)


def _threads(client, room: str, path: str = PATH):
    response = client.get(
        f"/topics/{room}/files/comments/threads", params={"path": path}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_thread_on_a_file_keeps_its_anchor_and_behaves_like_a_document_thread(client):
    room = _room(client)
    _file(client, room)
    made = _open(client, room)
    assert made.status_code == 200, made.text
    root = made.json()["data"]
    assert root["anchor"] == _anchor()
    assert root["anchor_quote"] == "第 3 页的一句"

    listed = _threads(client, room)
    assert listed["total"] == 1
    assert listed["data"][0]["comment"]["id"] == root["id"]
    assert listed["data"][0]["state"] == "open"
    assert listed["data"][0]["revision"] == 1

    prefix = f"/topics/{room}/files/comments/{root['id']}"
    body = _mutation(1, content="我也看到了")
    reply = client.post(prefix + "/replies", params={"path": PATH}, json=body)
    assert reply.status_code == 200, reply.text
    got = reply.json()["data"]
    assert got["revision"] == 2
    assert got["replies"][0]["sequence"] == 1
    assert got["replies"][0]["comment"]["author"] == root["author"]

    # 同一次重发：拿回同一份回执，不再写一条；改一个字段再发是冲突。
    assert (
        client.post(prefix + "/replies", params={"path": PATH}, json=body).json()
        == reply.json()
    )
    assert (
        client.post(
            prefix + "/replies",
            params={"path": PATH},
            json={**body, "content": "改了字"},
        ).status_code
        == 409
    )

    resolve_body = _mutation(2)
    resolved = client.post(
        prefix + "/resolve", params={"path": PATH}, json=resolve_body
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["data"]["state"] == "resolved"
    assert (
        client.post(
            prefix + "/replies", params={"path": PATH}, json=_mutation(3, content="x")
        ).status_code
        == 409
    )
    reopened = client.post(prefix + "/reopen", params={"path": PATH}, json=_mutation(3))
    assert reopened.status_code == 200
    assert reopened.json()["data"]["revision"] == 4
    # 存下的回执是那一刻的证据，不是此刻的线程状态。
    assert (
        client.post(
            prefix + "/resolve", params={"path": PATH}, json=resolve_body
        ).json()
        == resolved.json()
    )


def test_a_thread_on_one_file_is_not_a_thread_on_another(client):
    room = _room(client)
    _file(client, room)
    _file(client, room, OTHER)
    assert _open(client, room).status_code == 200
    assert _threads(client, room, OTHER)["total"] == 0
    # 指认说的是另一份文件：拒收。
    bad = _open(client, room, anchor=_anchor(OTHER))
    assert bad.status_code == 422, bad.text


def test_a_path_with_nothing_behind_it_takes_no_comment(client):
    """一份不在那儿的文件上开一条评论，谁也看不见——拒掉。

    指认也跟着指那一份（不然先撞上的是「指认说的是另一份文件」那条），这样这一
    条测的就只是「文件在不在」。"""
    room = _room(client)
    _file(client, room)
    missing = "output/没有这份.pdf"
    assert _open(client, room, path=missing, anchor=_anchor(missing)).status_code == 404


def test_an_archived_room_takes_no_new_comment(client):
    """归档的房间冻结，和它的文档一样：新评论进不去。"""
    room = _room(client)
    _file(client, room)

    async def archive():
        async with client.test_factory() as session:
            topic = await session.get(Topic, uuid.UUID(room))
            assert topic is not None
            topic.status = TopicStatus.archived
            await session.commit()

    asyncio.run(archive())
    assert _open(client, room).status_code == 422


def test_the_thread_hangs_on_the_file_and_on_no_document(client):
    room = _room(client)
    _file(client, room)
    root = _open(client, room).json()["data"]

    async def read():
        async with client.test_factory() as session:
            return await session.scalar(
                select(DocumentComment).where(
                    DocumentComment.id == uuid.UUID(root["id"])
                )
            )

    row = asyncio.run(read())
    assert row is not None
    assert row.room_path == PATH
    assert row.document_id is None
    assert row.room_id == uuid.UUID(room)
    assert row.anchor == _anchor()
