"""一份房间文件的实时协同：开一次门拿到它的协作状态名，协作服务按这个名字
加载和存回，存回的那一版落成这份文件自己的新一版。

规则都是用户说得出来的：
- 打开一份文本房间文件，拿回的名字每次都一样，而另一份文件是另一个名字。
- 不顺手的（不存在的、二进制的）开不了，理由说得出。
- 协作服务按名字存回来的那一版，读到的是它，旧的那一版仍取得回来。
- 存回来的一版和这一版内容一样时，不为它多记一版。
- 两个人同时打开同一份文件，输的那一个读到赢的那一行，调用方的交易不受牵连。
- 每存一次，这一行的「最后存储时间」都跟着走。
"""

import asyncio
import base64
import uuid
from datetime import UTC, datetime

from sqlalchemy import event

from app.core.sandbox_auth import mint_scoped_token
from app.domain.living_doc import collab, room_file_collab
from app.domain.project.models import RoomFileCollab
from app.domain.textfile import MAX_TEXT_BYTES
from tests.integration.conftest import post_project

PATH = "notes/记录.md"


def _room(client) -> tuple[str, str]:
    project = post_project(client, json={"name": "P"}).json()["data"]["id"]
    room = client.post(
        "/topics", json={"project_id": project, "title": "一起改一份笔记"}
    ).json()["data"]["id"]
    return project, room


def _agent(project: str, room: str) -> dict:
    return {
        "Authorization": f"Bearer {
            mint_scoped_token(project_id=project, topic_id=room, ttl_s=3600)
        }"
    }


def _put(client, project, room, data: bytes, path: str = PATH):
    return client.post(
        f"/topics/{room}/shown",
        headers=_agent(project, room),
        json={"path": path, "content_b64": base64.b64encode(data).decode()},
    )


def _open(client, room, path: str = PATH):
    return client.get(f"/topics/{room}/files/collab", params={"path": path})


def _store(client, name: str, content: str, *actors: str):
    """The collaboration service storing what its editors typed."""
    return client.put(
        f"/internal/collab/documents/{name}",
        json={
            "state": base64.b64encode(b"yjs").decode(),
            "content": content,
            "actors": list(actors),
            "suggestions": [],
        },
        headers={"Authorization": f"Bearer {collab._key('internal')}"},
    )


def test_opening_a_room_file_gives_a_name_that_holds(client):
    project, room = _room(client)
    _put(client, project, room, "第一版\n".encode())
    first = _open(client, room).json()["data"]
    assert first["enabled"] is True
    # The name carries the live state's id, and opening again is the same one.
    assert first["document"].startswith("roomfile:")
    assert _open(client, room).json()["data"]["document"] == first["document"]
    # Another file of the same room is a different live document.
    _put(client, project, room, b"another\n", path="notes/别的.md")
    assert (
        _open(client, room, path="notes/别的.md").json()["data"]["document"]
        != first["document"]
    )


def test_only_this_rooms_text_opens_live(client):
    project, room = _room(client)
    # Not there at all.
    assert _open(client, room, path="notes/没有.md").status_code == 404
    # A binary is not text: it does not open live (the office editor has it).
    _put(
        client,
        project,
        room,
        b"\x89PNG\r\n\x1a\n",
        path="notes/图.png",
    )
    refused = _open(client, room, path="notes/图.png").json()["data"]
    assert refused["enabled"] is False
    assert refused["reason"] == "unsupported"


def test_text_over_the_ceiling_does_not_open_live(client):
    # Its text is never read into a body at all (`too_large`). Seeding the
    # live state with anything — the empty string included — would let the
    # service's first store write it over the file.
    project, room = _room(client)
    _put(client, project, room, b"x" * (MAX_TEXT_BYTES + 1))
    refused = _open(client, room).json()["data"]
    assert refused["enabled"] is False
    assert refused["reason"] == "too_large"


def test_a_store_through_the_service_becomes_a_version_of_the_file(client):
    project, room = _room(client)
    _put(client, project, room, "第一版\n".encode())
    name = _open(client, room).json()["data"]["document"]
    stored = _store(client, name, "第一版\n第二行\n", "alice")
    assert stored.status_code == 200, stored.text
    # What a reader sees is the file, and its history kept the state before.
    assert client.get(f"/topics/{room}/files/raw", params={"path": PATH}).content == (
        "第一版\n第二行\n".encode()
    )
    history = client.get(
        f"/topics/{room}/files/revisions", params={"path": PATH}
    ).json()
    assert len(history["data"]) >= 2
    # The live state is what the open editors share, and it was kept: reopening
    # the file hands back the text the store left, under the same name.
    reloaded = _open(client, room).json()["data"]
    assert reloaded["document"] == name
    assert reloaded["content"] == "第一版\n第二行\n"


def test_a_store_that_moved_the_text_tells_the_open_editors(client, monkeypatch):
    # A store becomes a new version of the file; the editors still hold the old
    # text and everything derived from it (the history, the comment anchors) has
    # to be re-read. That is the frame a living document's store sends, and the
    # room-file store has to send it too — under the name the service knows.
    told: list[tuple[str, dict]] = []

    async def record(name: str, frame: dict):
        told.append((name, frame))

    monkeypatch.setattr(collab, "tell_named", record)
    project, room = _room(client)
    _put(client, project, room, "第一版\n".encode())
    name = _open(client, room).json()["data"]["document"]
    told.clear()  # opening a file tells nobody anything

    assert _store(client, name, "第一版\n第二行\n", "alice").status_code == 200
    assert told == [(name, {"type": "state", "resource": "doc"})]

    # A store that changed no text wrote no version, so there is nothing for the
    # editors to re-read: no frame either.
    told.clear()
    assert _store(client, name, "第一版\n第二行\n", "alice").status_code == 200
    assert told == []


def test_the_live_state_row_is_inserted_inside_the_savepoint(client):
    # Losing the create race must not abort the caller's transaction. That
    # only holds if the INSERT is inside the savepoint: begin_nested() flushes
    # pending objects first, so a row queued outside it would be written before
    # SAVEPOINT is even emitted.
    project, room = _room(client)
    engine = client.test_factory.kw["bind"].sync_engine
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    async def open_it():
        async with client.test_factory() as session:
            return await room_file_collab.open_for(
                session,
                project_id=uuid.UUID(project),
                room_id=uuid.UUID(room),
                path=PATH,
                content="第一版\n",
            )

    event.listen(engine, "before_cursor_execute", record)
    try:
        asyncio.run(open_it())
    finally:
        event.remove(engine, "before_cursor_execute", record)

    def index_of(prefix: str) -> int:
        return next(
            i for i, s in enumerate(seen) if s.lstrip().lower().startswith(prefix)
        )

    assert index_of("insert into room_file_collab") > index_of("savepoint")


def test_a_store_moves_the_live_states_storage_time(client):
    project, room = _room(client)
    _put(client, project, room, "第一版\n".encode())
    name = _open(client, room).json()["data"]["document"]
    collab_id = uuid.UUID(name.split(":", 1)[1])

    async def make_it_old():
        async with client.test_factory() as session:
            row = await session.get(RoomFileCollab, collab_id)
            row.updated_at = datetime(2000, 1, 1, tzinfo=UTC)
            await session.commit()

    asyncio.run(make_it_old())
    assert _store(client, name, "第二版\n", "alice").status_code == 200

    async def storage_time():
        async with client.test_factory() as session:
            return (await session.get(RoomFileCollab, collab_id)).updated_at

    assert asyncio.run(storage_time()).year >= 2024


def test_a_store_that_changed_no_text_writes_no_new_version(client):
    project, room = _room(client)
    _put(client, project, room, "只此一版\n".encode())
    name = _open(client, room).json()["data"]["document"]
    before = len(
        client.get(f"/topics/{room}/files/revisions", params={"path": PATH}).json()[
            "data"
        ]
    )
    # The service's first conversion: the same text, respelled by the editor.
    assert _store(client, name, "只此一版\n", "system").status_code == 200
    after = len(
        client.get(f"/topics/{room}/files/revisions", params={"path": PATH}).json()[
            "data"
        ]
    )
    assert after == before
