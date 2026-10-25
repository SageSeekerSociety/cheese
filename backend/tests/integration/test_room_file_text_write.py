"""在预览格里改一份文本房间文件，存回那一份本身。

规则都是用户说得出来的：
- 改了存下去，读到的是新内容，旧的那一版仍然取得回来。
- 打开之后别人先存过，再存我这版要报冲突，不能把对方盖掉。
- 资料库原件不是这个房间的文件，改不了（写进去别人读到的会是同名的一份房间文件）。
- 只带内容、不带你读的那一版，等于没读就写：不接收（那份文件在不在都先问过）。
- 一份二进制也能有这个地址：把文本写进去会把它毁了，所以照文本那一条判据拒。
"""

import base64

from app.core.sandbox_auth import mint_scoped_token
from app.domain.textfile import content_version
from tests.integration.conftest import post_project

PATH = "notes/记录.md"


def _room(client) -> tuple[str, str]:
    project = post_project(client, json={"name": "P"}).json()["data"]["id"]
    room = client.post(
        "/topics", json={"project_id": project, "title": "改一份笔记"}
    ).json()["data"]["id"]
    return project, room


def _agent(project: str, room: str) -> dict:
    return {
        "X-Cheese-Token": mint_scoped_token(
            project_id=project, topic_id=room, ttl_s=3600
        )
    }


def _put(client, project, room, data: bytes, path: str = PATH):
    """把一份文件放进房间（走芝士摆东西那条已有的口，和这次改动无关）。"""
    return client.post(
        f"/topics/{room}/shown",
        headers=_agent(project, room),
        json={"path": path, "content_b64": base64.b64encode(data).decode()},
    )


def _write_text(client, room, content, version=None, path: str = PATH):
    body = {"path": path, "content": content}
    if version is not None:
        body["version"] = version
    return client.post(f"/topics/{room}/files/text", json=body)


def _read(client, room, path: str = PATH) -> bytes:
    r = client.get(f"/topics/{room}/files/raw", params={"path": path})
    assert r.status_code == 200, r.text
    return r.content


def test_editing_a_text_file_saves_it_back_as_a_new_version(client):
    project, room = _room(client)
    before = "# 一\n".encode()
    assert _put(client, project, room, before).status_code == 200

    saved = _write_text(client, room, "# 一\n\n补了一段。\n", content_version(before))

    assert saved.status_code == 200, saved.text
    assert _read(client, room) == "# 一\n\n补了一段。\n".encode()
    # 旧的那一版还在，取得回来。
    hist = client.get(f"/topics/{room}/files/revisions", params={"path": PATH}).json()[
        "data"
    ]["data"]
    assert [h["seq"] for h in hist] == [2, 1]


def test_a_save_based_on_an_old_version_does_not_overwrite_the_newer_one(client):
    project, room = _room(client)
    v1 = b"v1"
    assert _put(client, project, room, v1).status_code == 200
    read_at = content_version(v1)
    # 期间别人存了一版。
    assert _write_text(client, room, "别人写的", read_at).status_code == 200

    late = _write_text(client, room, "我这版", read_at)

    assert late.status_code == 409
    assert late.json()["error"]["data"]["version"] == content_version(
        "别人写的".encode()
    )
    assert _read(client, room) == "别人写的".encode()

    # 按最新那一版读过再写，就写得进去。
    again = _write_text(client, room, "我这版", content_version("别人写的".encode()))
    assert again.status_code == 200, again.text
    assert _read(client, room) == "我这版".encode()


def test_a_library_original_is_not_this_rooms_file_and_cannot_be_edited(client):
    project, room = _room(client)
    # 带上版本，测的才是「这个地址不是这个房间的文件」，不是前面那条「没读就写」。
    r = _write_text(client, room, "改一下", "whatever", path="library/某个原件.md")
    assert r.status_code in (400, 422), r.text


def test_a_save_that_did_not_read_the_file_is_refused(client):
    project, room = _room(client)
    before = "# 一\n".encode()
    assert _put(client, project, room, before).status_code == 200

    blind = _write_text(client, room, "# 我把整份重写了\n")

    assert blind.status_code == 422, blind.text
    # 拒了就是拒了：文件没动。
    assert _read(client, room) == before


def test_text_is_not_written_over_a_binary_file(client):
    project, room = _room(client)
    deck = b"PK\x03\x04\x00\x00\x00\x00binary bytes, not utf-8 \xff\xfe"
    assert _put(client, project, room, deck, path="deck.pptx").status_code == 200

    r = _write_text(client, room, "改成文字", content_version(deck), path="deck.pptx")

    assert r.status_code == 422, r.text
    assert _read(client, room, path="deck.pptx") == deck
