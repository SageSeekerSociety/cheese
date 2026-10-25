"""一份房间文件的实时协同：开一次门拿到它的协作状态名，协作服务按这个名字
加载和存回，存回的那一版落成这份文件自己的新一版。

规则都是用户说得出来的：
- 打开一份文本房间文件，拿回的名字每次都一样，而另一份文件是另一个名字。
- 不顺手的（不存在的、二进制的）开不了，理由说得出。
- 协作服务按名字存回来的那一版，读到的是它，旧的那一版仍取得回来。
- 存回来的一版和这一版内容一样时，不为它多记一版。
"""

import base64

from app.core.sandbox_auth import mint_scoped_token
from app.domain.living_doc import collab
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
        "X-Cheese-Token": mint_scoped_token(
            project_id=project, topic_id=room, ttl_s=3600
        )
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
    history = client.get(f"/topics/{room}/files/revisions", params={"path": PATH}).json()
    assert len(history["data"]) >= 2
    # The live state is what the open editors share, and it was kept: reopening
    # the file hands back the text the store left, under the same name.
    reloaded = _open(client, room).json()["data"]
    assert reloaded["document"] == name
    assert reloaded["content"] == "第一版\n第二行\n"


def test_a_store_that_changed_no_text_writes_no_new_version(client):
    project, room = _room(client)
    _put(client, project, room, "只此一版\n".encode())
    name = _open(client, room).json()["data"]["document"]
    before = len(
        client.get(f"/topics/{room}/files/revisions", params={"path": PATH})
        .json()["data"]
    )
    # The service's first conversion: the same text, respelled by the editor.
    assert _store(client, name, "只此一版\n", "system").status_code == 200
    after = len(
        client.get(f"/topics/{room}/files/revisions", params={"path": PATH})
        .json()["data"]
    )
    assert after == before
