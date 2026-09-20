"""房间里摆出来的东西，以及把其中一份存进项目 (#1085 结论四)。

房间里的文件不是项目产物：改完在那个房间里拿走，事情就结束了。升级是一个动作 ——
按了才算 —— 所以这里问的是：摆出来的东西列不列得全、谁能按那一下、按下去之后项目
里多了什么。

「按下去之后」有三件事必须同时成立，少一件这份东西就是半个：文件进了项目那棵树、
清单上多了一项、它是第 1 版（版本是数出来的，所以必须真的留下一张采纳了的卡）。
"""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from app.domain.workspace import service as ws


def _project(client) -> str:
    r = client.post("/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _room(client, project_id: str, title: str = "改一份文件") -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": title})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _agent_headers(project_id: str, room_id: str) -> dict:
    """一轮里铸出来的那种凭据 —— 芝士 摆东西出来用的就是它。"""
    return {
        "X-Cheese-Token": mint_scoped_token(
            project_id=project_id, topic_id=room_id, ttl_s=3600
        )
    }


def _show(
    client, project_id: str, room_id: str, path: str, content: str = "<h1>x</h1>"
):
    return client.post(
        f"/topics/{room_id}/shown",
        headers=_agent_headers(project_id, room_id),
        json={"path": path, "content": content},
    )


def _shown(client, room_id: str) -> list[dict]:
    r = client.get(f"/topics/{room_id}/shown")
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _save(client, room_id: str, path: str, **body):
    """按下「保存到项目」的是人 —— 这里就是建这个项目的那个人。"""
    return client.post(
        f"/topics/{room_id}/shown/save",
        json={"path": path, **body},
    )


def test_a_room_lists_everything_it_has_shown_newest_first(client):
    project_id = _project(client)
    room_id = _room(client, project_id)

    assert _show(client, project_id, room_id, "report.html").status_code == 200
    assert _show(client, project_id, room_id, "chart.svg", "<svg />").status_code == 200

    assert [row["path"] for row in _shown(client, room_id)] == [
        "chart.svg",
        "report.html",
    ]


def test_showing_the_same_thing_again_is_one_thing_not_two(client):
    """重新摆一次是同一个东西的新一次渲染，不是又做了一样东西。"""
    project_id = _project(client)
    room_id = _room(client, project_id)

    _show(client, project_id, room_id, "report.html", "<h1>一稿</h1>")
    _show(client, project_id, room_id, "report.html", "<h1>二稿</h1>")

    assert [row["path"] for row in _shown(client, room_id)] == ["report.html"]


def test_saving_puts_the_file_in_the_project_tree_and_on_the_manifest(client):
    project_id = _project(client)
    room_id = _room(client, project_id)
    _show(client, project_id, room_id, "评审简报.html", "<h1>定稿</h1>")

    saved = _save(client, room_id, "评审简报.html")
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["version"] == 1

    # 清单上多了一项，而且它已经是第 1 版 —— 保存本身就是一次交付。
    rows = client.get(f"/projects/{project_id}/artifacts").json()["data"]["data"]
    assert [(row["name"], row["version"]) for row in rows] == [("评审简报.html", 1)]

    # 文件进了项目那棵树：它就是源，主干上真的有这一次提交。
    branch, head = ws.base_branch_head(uuid.UUID(project_id))
    assert any(
        entry["path"] == "评审简报.html"
        for entry in ws.committed_files(uuid.UUID(project_id), head)
    )


def test_the_saved_version_can_be_taken_again_from_the_artifact_page(client):
    project_id = _project(client)
    room_id = _room(client, project_id)
    _show(client, project_id, room_id, "评审简报.html", "<h1>定稿</h1>")
    _save(client, room_id, "评审简报.html")

    artifact_id = client.get(f"/projects/{project_id}/artifacts").json()["data"][
        "data"
    ][0]["id"]
    detail = client.get(f"/projects/{project_id}/artifacts/{artifact_id}").json()[
        "data"
    ]
    (version,) = detail["versions"]
    assert version["kind"] == "file"
    # 这一版是谁交的 —— 按下保存的那个人；房间里那句话说的必须是同一个人。
    said = client.get(f"/topics/{room_id}/blocks").json()["data"]["data"]
    told = " ".join(
        f"{block.get('content') or ''} {block.get('meta') or ''}" for block in said
    )
    assert version["decided_by"]
    assert version["decided_by"] in told

    got = client.get(
        f"/projects/{project_id}/artifacts/{artifact_id}"
        f"/versions/{version['card_id']}/file"
    )
    assert got.status_code == 200
    assert got.content == "<h1>定稿</h1>".encode()


def test_saving_the_next_one_onto_the_same_item_is_its_next_version(client):
    project_id = _project(client)
    room_id = _room(client, project_id)
    _show(client, project_id, room_id, "简报.html", "<h1>一稿</h1>")
    first = _save(client, room_id, "简报.html")
    artifact_id = first.json()["data"]["artifact"]["id"]

    _show(client, project_id, room_id, "简报.html", "<h1>二稿</h1>")
    again = _save(client, room_id, "简报.html", artifact=artifact_id)

    assert again.status_code == 200, again.text
    assert again.json()["data"]["version"] == 2
    rows = client.get(f"/projects/{project_id}/artifacts").json()["data"]["data"]
    assert [(row["name"], row["version"]) for row in rows] == [("简报.html", 2)]


def test_a_saved_name_can_be_given_by_the_person(client):
    """文件名不一定是这样东西的名字 —— 起名是人的判断。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    _show(client, project_id, room_id, "out.html", "<h1>x</h1>")

    _save(client, room_id, "out.html", name="项目官网")

    rows = client.get(f"/projects/{project_id}/artifacts").json()["data"]["data"]
    assert [row["name"] for row in rows] == ["项目官网"]


def test_cheese_can_show_but_cannot_save(client):
    """摆出来是 芝士 的事，进不进项目是人的判断。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    _show(client, project_id, room_id, "report.html")

    denied = client.post(
        f"/topics/{room_id}/shown/save",
        headers=_agent_headers(project_id, room_id),
        json={"path": "report.html"},
    )
    assert denied.status_code in (401, 403), denied.text
    assert client.get(f"/projects/{project_id}/artifacts").json()["data"]["data"] == []


def test_saving_something_the_room_does_not_have_is_refused(client):
    project_id = _project(client)
    room_id = _room(client, project_id)

    missing = _save(client, room_id, "没有这一份.html")

    assert missing.status_code >= 400
    assert client.get(f"/projects/{project_id}/artifacts").json()["data"]["data"] == []


def test_saving_the_identical_bytes_again_lands_no_empty_commit(client):
    """内容没变的那一次保存不该在历史里留下一次「什么都没改」。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    _show(client, project_id, room_id, "简报.html", "<h1>同一份</h1>")
    first = _save(client, room_id, "简报.html")
    artifact_id = first.json()["data"]["artifact"]["id"]
    head = ws.base_branch_head(uuid.UUID(project_id))[1]

    again = _save(client, room_id, "简报.html", artifact=artifact_id)

    assert again.status_code == 200, again.text
    assert ws.base_branch_head(uuid.UUID(project_id))[1] == head
    # 但它仍然是一次交付：人按了一下，就是又交了一版。
    assert again.json()["data"]["version"] == 2
