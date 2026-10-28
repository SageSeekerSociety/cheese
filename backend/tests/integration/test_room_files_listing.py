"""频道自己的文件区，摆成一页能看的清单。

在这条路由之前，房间文件只有「按路径取一份」的读法：要看到里面有什么，得先知道
那份文件叫什么。定时任务交回的结果、芝士交付的文档、有人放进来的文件都落在同一个
目录里，而那台机器上的人看不见它。这里问的是：那份目录本身列不列得全、列出来的
形状对不对、每一行说得出是谁放的、谁能看。

列的是**这个房间自己的**目录（``.room-files/<项目>/<房间>``），不是资料库；点开头
的临时层不算文件，目录本身也不算一份，一条消息的配图也不算 —— 它和房间文件住在
同一棵树里（按地址读得到，消息里的图要显示），但属于那条消息，不是这个房间的文件。
"""

import base64
import os
import uuid

from app.core.sandbox_auth import mint_scoped_token
from app.domain.library import service as library
from tests.integration.conftest import post_project, session_auth_headers

# 真图：1x1 的透明 PNG，67 字节。贴图那条路不解析图片，但用真字节省得日后有人
# 以为这里量的是「随便一串字节也能贴」。
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _room(client) -> tuple[str, str]:
    project = post_project(client, json={"name": "P"}).json()["data"]["id"]
    room = client.post(
        "/topics", json={"project_id": project, "title": "文献综述"}
    ).json()["data"]["id"]
    return project, room


def _files(client, room: str, handle: str = "owner"):
    response = client.get(f"/topics/{room}/files", headers=session_auth_headers(handle))
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _write(
    client, project: str, room: str, path: str, data: bytes, mtime: float | None = None
):
    """把一份文件放进房间自己的目录 —— 干活的那台机器干的就是这件事。"""
    library.write_room_file(uuid.UUID(project), uuid.UUID(room), path, data)
    if mtime is not None:
        target = library.room_files_root(uuid.UUID(project), uuid.UUID(room)) / path
        os.utime(target, (mtime, mtime))


def _paste(client, room: str, name: str) -> str:
    """贴一张图进来（剪贴板那条路），返回它在房间里的地址。"""
    response = client.post(
        f"/topics/{room}/attachments",
        headers=session_auth_headers("owner"),
        data={"origin": "clipboard"},
        files={"file": (name, PNG_1PX, "image/png")},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["path"]


def _agent(project: str, room: str) -> dict:
    return {
        "Authorization": f"Bearer {
            mint_scoped_token(project_id=project, topic_id=room, ttl_s=3600)
        }"
    }


def _show(client, project: str, room: str, path: str, data: bytes, **extra) -> int:
    """芝士 把一份东西交回房间（``cheese show`` 那条路），返回状态码。"""
    response = client.post(
        f"/topics/{room}/shown",
        headers=_agent(project, room),
        json={
            "path": path,
            "content_b64": base64.b64encode(data).decode(),
            **extra,
        },
    )
    return response.status_code


def test_a_room_lists_its_own_files_newest_first(client):
    """芝士交付的文档、定时任务的结果、有人放进来的文件，都在同一份清单里，最近改的
    在最前，带路径、大小和改动时间。"""
    project, room = _room(client)
    _write(client, project, room, "封面.png", b"a" * 10, mtime=1_700_000_000)
    _write(
        client, project, room, "runs/2026-09-14/周报.md", b"b" * 20, mtime=1_700_000_200
    )
    _write(client, project, room, "文献综述.docx", b"c" * 30, mtime=1_700_000_100)

    body = _files(client, room)

    assert [row["path"] for row in body["data"]] == [
        "runs/2026-09-14/周报.md",
        "文献综述.docx",
        "封面.png",
    ]
    assert body["data"][0]["bytes"] == 20
    assert body["data"][0]["modified_at"].startswith("2023-11-14")
    assert body["total"] == 3
    assert body["truncated"] is False


def test_a_room_with_nothing_in_it_is_an_empty_list_not_an_error(client):
    """一份都没有是「暂无文件」，不是失败 —— 页面要能说这句话。"""
    _, room = _room(client)

    body = _files(client, room)

    assert body["data"] == []
    assert body["total"] == 0
    assert body["truncated"] is False


def test_hidden_layers_and_directories_are_not_files(client):
    """编辑器留下的临时文件（点开头的层）不是给人看的；目录本身也不是一份文件。"""
    project, room = _room(client)
    _write(client, project, room, "报告.md", "正文".encode())
    _write(client, project, room, ".cache/scratch.tmp", "临时".encode())
    _write(client, project, room, "runs/.snapshot.json", "中间态".encode())
    _write(client, project, room, "runs/2026-09-14/keep.txt", "结果".encode())

    paths = [row["path"] for row in _files(client, room)["data"]]

    assert paths == ["runs/2026-09-14/keep.txt", "报告.md"]


def test_over_the_cap_the_page_says_so_instead_of_hiding_it(client, monkeypatch):
    """目录失控（远超正常量级）时只列最近改的那几份，并且明说不是全部 —— 不闷着。"""
    project, room = _room(client)
    for index in range(6):
        _write(client, project, room, f"{index}.md", b"x", mtime=1_700_000_000 + index)
    monkeypatch.setattr(library, "MAX_LISTED_ROOM_FILES", 2)

    body = _files(client, room)

    assert [row["path"] for row in body["data"]] == ["5.md", "4.md"]
    assert body["total"] == 6
    assert body["truncated"] is True


def test_only_people_in_the_room_can_read_the_listing(client):
    """房间文件里可能有私下的东西，所以这份清单和取一份字节一样要鉴权：不在这个项目
    里的人看不到。"""
    project, room = _room(client)
    _write(client, project, room, "报告.md", "正文".encode())

    response = client.get(
        f"/topics/{room}/files", headers=session_auth_headers("outsider")
    )

    assert response.status_code in (403, 404)


def test_a_pasted_screenshot_is_not_a_room_file(client):
    """贴进来的图属于那条消息，不属于这个房间：不在清单上（资料库里已经有它了，这
    再来一份是同一张图说两遍），但按地址照读 —— 消息里那张图得显示得出来。"""
    project, room = _room(client)
    _write(client, project, room, "报告.md", "正文".encode())

    pasted = _paste(client, room, "screenshot.png")

    assert pasted.startswith("uploads/")
    body = _files(client, room)
    assert [row["path"] for row in body["data"]] == ["报告.md"]
    assert body["total"] == 1
    raw = client.get(
        f"/topics/{room}/files/raw",
        params={"path": pasted},
        headers=session_auth_headers("owner"),
    )
    assert raw.status_code == 200, raw.text
    assert raw.content == PNG_1PX


def test_each_row_says_who_left_the_file_and_through_which_door(client):
    """每一行要说得出是谁、从哪条路放下的：芝士交回来的、人自己摆出来的、直接落在
    目录里的，各有各的说法；说不出来的就照实说没有，不编一个。"""
    project, room = _room(client)
    _write(client, project, room, "手放的.md", "直接落在目录里".encode())
    assert _show(client, project, room, "output/报告.docx", "芝士交的".encode()) == 200
    person = client.post(
        f"/topics/{room}/shown",
        headers=session_auth_headers("owner"),
        json={"path": "人摆的.md", "content": "人摆的"},
    )
    assert person.status_code == 200, person.text

    rows = {row["path"]: row for row in _files(client, room)["data"]}

    by_agent = rows["output/报告.docx"]
    assert by_agent["author_kind"] == "agent"
    assert by_agent["source"] == "ai"
    assert by_agent["author"]

    by_person = rows["人摆的.md"]
    assert by_person["author_kind"] == "human"
    assert by_person["source"] == "upload"
    assert by_person["author"] == "owner"

    loose = rows["手放的.md"]
    assert loose["bytes"] == len("直接落在目录里".encode())
    assert loose["author"] is None
    assert loose["author_kind"] is None
    assert loose["source"] is None
