"""房间里摆出来的东西，以及把其中一份留进资料库 (#1085 结论四)。

房间里的文件不是项目产物：改完在那个房间里拿走，事情就结束了。想留下来以后还用是
**一个动作** —— 按了才算 —— 所以这里问的是：摆出来的东西列不列得全、谁能按那一
下、按下去之后项目里多了什么。

同样要紧的是**没多什么**：资料库里多一份，而产物清单一个字没变、主干一个提交都没
多。留着要用的东西是资料；清单上的一项是要交出去的东西，那由交付长出来。
"""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from tests.support import git_store


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


def _save(client, room_id: str, path: str):
    """按下「保存到资料库」的是人 —— 这里就是建这个项目的那个人。"""
    return client.post(f"/topics/{room_id}/shown/save", json={"path": path})


def _library(client, project_id: str) -> list[str]:
    r = client.get(f"/projects/{project_id}/library")
    assert r.status_code == 200, r.text
    return [entry["path"] for entry in r.json()["data"]["data"]]


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


def test_saving_puts_it_in_the_library_under_its_own_name(client):
    project_id = _project(client)
    room_id = _room(client, project_id)
    _show(client, project_id, room_id, "out/评审简报.html", "<h1>定稿</h1>")

    saved = _save(client, room_id, "out/评审简报.html")

    assert saved.status_code == 200, saved.text
    # 目录是那一轮的工作痕迹，名字才是它的身份 —— 资料库按名字寻址。
    assert saved.json()["data"]["name"] == "评审简报.html"
    assert _library(client, project_id) == ["评审简报.html"]


def test_every_room_can_read_what_one_room_saved(client):
    """留下来就是为了以后还用得上 —— 资料库是项目级的。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    other = _room(client, project_id, "另一个房间")
    _show(client, project_id, room_id, "评审简报.html", "<h1>定稿</h1>")
    _save(client, room_id, "评审简报.html")

    got = client.get(
        f"/projects/{project_id}/library/raw",
        params={"path": "评审简报.html", "topic": other},
    )

    assert got.status_code == 200, got.text
    assert got.content == "<h1>定稿</h1>".encode()


def test_saving_twice_keeps_both_instead_of_overwriting(client):
    """同名不覆盖，跟资料库自己的规矩走：谁也说不准第二份是不是第一份的新版。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    _show(client, project_id, room_id, "简报.html", "<h1>一稿</h1>")
    _save(client, room_id, "简报.html")
    _show(client, project_id, room_id, "简报.html", "<h1>二稿</h1>")

    again = _save(client, room_id, "简报.html")

    assert again.json()["data"]["name"] == "简报(2).html"
    assert sorted(_library(client, project_id)) == ["简报(2).html", "简报.html"]


def test_saving_adds_nothing_to_the_manifest_and_nothing_to_the_trunk(client):
    """留着以后用 ≠ 交出去。清单由交付长出来，而这一下不是交付。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    _show(client, project_id, room_id, "评审简报.html", "<h1>定稿</h1>")
    repository = git_store.path(uuid.UUID(project_id))
    head = git_store.git(repository, "rev-parse", "main")

    _save(client, room_id, "评审简报.html")

    assert client.get(f"/projects/{project_id}/artifacts").json()["data"]["data"] == []
    assert git_store.git(repository, "rev-parse", "main") == head


def test_cheese_can_show_but_cannot_save(client):
    """摆出来是 芝士 的事；这份东西以后还用不用得上，是人的判断。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    _show(client, project_id, room_id, "report.html")

    denied = client.post(
        f"/topics/{room_id}/shown/save",
        headers=_agent_headers(project_id, room_id),
        json={"path": "report.html"},
    )

    assert denied.status_code in (401, 403), denied.text
    assert _library(client, project_id) == []


def test_saving_something_the_room_does_not_have_is_refused(client):
    project_id = _project(client)
    room_id = _room(client, project_id)

    missing = _save(client, room_id, "没有这一份.html")

    assert missing.status_code >= 400
    assert _library(client, project_id) == []


def test_the_room_is_told_what_was_saved(client):
    """留进资料库是这个房间里发生的一件事，读这个房间的人应该看得到。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    _show(client, project_id, room_id, "评审简报.html", "<h1>定稿</h1>")

    _save(client, room_id, "评审简报.html")

    said = client.get(f"/topics/{room_id}/blocks").json()["data"]["data"]
    assert any("评审简报.html 已存进资料库" in (b.get("content") or "") for b in said)
