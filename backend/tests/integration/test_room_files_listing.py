"""频道自己的文件区，摆成一页能看的清单。

在这条路由之前，房间文件只有「按路径取一份」的读法：要看到里面有什么，得先知道
那份文件叫什么。定时任务交回的结果、人贴进来的截图、芝士交付的文档都落在同一个
目录里，而那台机器上的人看不见它。这里问的是：那份目录本身列不列得全、列出来的
形状对不对、谁能看。

列的是**这个房间自己的**目录（``.room-files/<项目>/<房间>``），不是资料库；点开头
的临时层不算文件，目录本身也不算一份。
"""

import os
import uuid

from app.domain.library import service as library
from tests.integration.conftest import post_project, session_auth_headers


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


def _write(client, project: str, room: str, path: str, data: bytes, mtime: float | None = None):
    """把一份文件放进房间自己的目录 —— 干活的那台机器干的就是这件事。"""
    library.write_room_file(uuid.UUID(project), uuid.UUID(room), path, data)
    if mtime is not None:
        target = library.room_files_root(uuid.UUID(project), uuid.UUID(room)) / path
        os.utime(target, (mtime, mtime))


def test_a_room_lists_its_own_files_newest_first(client):
    """人贴进来的图、芝士交付的文档、定时任务的结果，都在同一份清单里，最近改的在
    最前，带路径、大小和改动时间。"""
    project, room = _room(client)
    _write(client, project, room, "screenshot.png", b"a" * 10, mtime=1_700_000_000)
    _write(client, project, room, "runs/2026-09-14/周报.md", b"b" * 20, mtime=1_700_000_200)
    _write(client, project, room, "文献综述.docx", b"c" * 30, mtime=1_700_000_100)

    body = _files(client, room)

    assert [row["path"] for row in body["data"]] == [
        "runs/2026-09-14/周报.md",
        "文献综述.docx",
        "screenshot.png",
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
        _write(
            client, project, room, f"{index}.md", b"x", mtime=1_700_000_000 + index
        )
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
