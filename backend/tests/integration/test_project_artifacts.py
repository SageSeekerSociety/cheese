"""产物清单的一生 —— 交付创建它，交付给它加版本，没交付成的不留在清单上 (#1085)。

沿用和新建是两个动作，所以这里每个错法都要有自己的一句话：沿用一个清单上没有的名
字、新建一个已经在清单上的名字、两个都给、两个都不给。写错名字本身不会报错（`报
告` 和 `结题报告` 都合法），把它变成一次当场的报错正是这两个动作存在的理由。
"""

import uuid

from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import session_auth_headers


def _project(client) -> str:
    r = client.post("/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _room(client, project_id: str, title: str = "做一个东西") -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": title})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _file_card(client, room_id: str, **artifact):
    """递一张卡。`artifact` / `new_artifact` 原样传下去，包括一个都不给。"""
    body = {
        "change_subject": "chore(test): file an accept card",
        "reviewer_handle": "alice",
        **artifact,
    }
    return client.post(
        f"/topics/{room_id}/tasks/{delivery_task_id(client, room_id)}/accept-card",
        headers=delivery_headers(client, room_id),
        json=body,
    )


def _manifest(client, project_id: str) -> list[tuple[str, int]]:
    r = client.get(f"/projects/{project_id}/artifacts")
    assert r.status_code == 200, r.text
    return [(a["name"], a["version"]) for a in r.json()["data"]["data"]]


def _decide(client, card_id: str, action: str):
    return client.post(
        f"/accept-cards/{card_id}/{action}",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )


def _lines(client, room_id: str) -> list[str]:
    r = client.get(f"/topics/{room_id}/blocks")
    assert r.status_code == 200
    return [b.get("content") or "" for b in r.json()["data"]["data"]]


def _notice_detail(client, room_id: str, needle: str) -> str:
    r = client.get(f"/topics/{room_id}/blocks")
    assert r.status_code == 200
    for block in r.json()["data"]["data"]:
        if needle in (block.get("content") or ""):
            return (block.get("meta") or {}).get("detail") or ""
    raise AssertionError(f"房间里没有说 {needle} 的那一行")


# --- 生：只有交付能创建一项 -------------------------------------------------


def test_declaring_a_new_artifact_puts_it_on_the_list(client):
    pid = _project(client)
    rid = _room(client, pid)

    r = _file_card(client, rid, new_artifact="结题报告")

    assert r.status_code == 200, r.text
    assert r.json()["data"]["artifact"]["name"] == "结题报告"
    assert r.json()["data"]["artifact"]["version"] == 0
    # 还没落地，但有人正在交付它 —— 清单上点得到，版本是 0。
    assert _manifest(client, pid) == [("结题报告", 0)]


def test_a_new_artifact_is_said_out_loud_next_to_what_the_project_already_has(client):
    pid = _project(client)
    first = _room(client, pid, "第一轮")
    _file_card(client, first, new_artifact="结题报告")
    second = _room(client, pid, "第二轮")

    _file_card(client, second, new_artifact="报告")

    # 新建是会长出垃圾的那一下，所以房间里当场有一行说它是新的……
    assert any("新建了产物《报告》" in line for line in _lines(client, second))
    # ……而判断「这是不是刚才那一项换了个说法」要两个名字摆在一起。
    detail = _notice_detail(client, second, "新建了产物《报告》")
    assert "《结题报告》" in detail and "《报告》" in detail


def test_the_list_has_no_other_way_in(client):
    """没有 POST：清单上的东西只能从交付进来。"""
    pid = _project(client)

    r = client.post(f"/projects/{pid}/artifacts", json={"name": "结题报告"})

    assert r.status_code == 405
    assert _manifest(client, pid) == []


# --- 长：沿用同一项 ---------------------------------------------------------


def test_reusing_the_id_delivers_the_same_artifact_again(client):
    pid = _project(client)
    first = _room(client, pid, "第一轮")
    second = _room(client, pid, "第二轮")
    declared = _file_card(client, first, new_artifact="结题报告").json()["data"]

    r = _file_card(client, second, artifact=declared["artifact"]["id"])

    assert r.status_code == 200, r.text
    assert r.json()["data"]["artifact"]["id"] == declared["artifact"]["id"]
    assert _manifest(client, pid) == [("结题报告", 0)]
    assert not any("新建了产物" in line for line in _lines(client, second))


def test_naming_the_artifact_instead_of_pointing_at_it_says_the_id(client):
    """沿用只认 id：名字写错不报错，所以按名字认的手滑会留在清单上。"""
    pid = _project(client)
    first = _room(client, pid, "第一轮")
    declared = _file_card(client, first, new_artifact="结题报告").json()["data"]
    second = _room(client, pid, "第二轮")

    r = _file_card(client, second, artifact="结题报告")

    assert r.status_code == 422
    assert declared["artifact"]["id"] in r.json()["message"]
    assert _manifest(client, pid) == [("结题报告", 0)]


def test_a_new_name_copied_with_its_brackets_loses_them(client):
    """房间里和清单上都把产物写成《结题报告》，照抄时括号会跟着进来。"""
    pid = _project(client)
    rid = _room(client, pid)

    r = _file_card(client, rid, new_artifact="《结题报告》")

    assert r.status_code == 200, r.text
    assert r.json()["data"]["artifact"]["name"] == "结题报告"


def test_a_version_is_a_delivery_that_landed(client):
    pid = _project(client)
    rid = _room(client, pid)
    cid = _file_card(client, rid, new_artifact="结题报告").json()["data"]["id"]

    # 递卡不是一版：这张卡还可能被驳回。
    assert _manifest(client, pid) == [("结题报告", 0)]

    assert _decide(client, cid, "accept").status_code == 200

    assert _manifest(client, pid) == [("结题报告", 1)]


# --- 灭：没交付成的不留在清单上 ---------------------------------------------


def test_a_rejected_delivery_leaves_nothing_on_the_list(client):
    pid = _project(client)
    rid = _room(client, pid)
    cid = _file_card(client, rid, new_artifact="结题报告").json()["data"]["id"]

    assert _decide(client, cid, "reject").status_code == 200

    # 什么都没交出去，也没有人正在交 —— 清单没什么可说的。
    assert _manifest(client, pid) == []


def test_the_same_name_declared_again_lands_on_the_same_item(client):
    """驳回之后重来一次，是同一项的第一版，不是另一项。"""
    pid = _project(client)
    rid = _room(client, pid)
    first = _file_card(client, rid, new_artifact="结题报告").json()["data"]
    _decide(client, first["id"], "reject")

    again = _file_card(client, rid, new_artifact="结题报告")

    assert again.status_code == 200, again.text
    assert again.json()["data"]["artifact"]["id"] == first["artifact"]["id"]


def test_revoking_the_only_delivery_takes_the_item_back_off_the_list(client):
    pid = _project(client)
    rid = _room(client, pid)
    cid = _file_card(client, rid, new_artifact="结题报告").json()["data"]["id"]
    _decide(client, cid, "accept")

    assert _decide(client, cid, "revoke").status_code == 200

    assert _manifest(client, pid) == []


# --- 两个动作各自的打回 -----------------------------------------------------


def test_reusing_an_id_the_list_does_not_have_is_refused(client):
    pid = _project(client)
    first = _room(client, pid, "第一轮")
    declared = _file_card(client, first, new_artifact="结题报告").json()["data"]
    second = _room(client, pid, "第二轮")

    r = _file_card(client, second, artifact=str(uuid.uuid4()))

    assert r.status_code == 422
    message = r.json()["message"]
    # 打回要教得会：清单上有什么（连 id），以及新建该怎么说。
    assert "《结题报告》" in message
    assert declared["artifact"]["id"] in message
    assert "new_artifact" in message


def test_declaring_a_name_the_list_already_has_is_refused(client):
    pid = _project(client)
    first = _room(client, pid, "第一轮")
    _file_card(client, first, new_artifact="结题报告")
    second = _room(client, pid, "第二轮")

    r = _file_card(client, second, new_artifact="结题报告")

    assert r.status_code == 422
    assert "artifact" in r.json()["message"]
    assert _manifest(client, pid) == [("结题报告", 0)]


def test_a_delivery_that_names_neither_is_refused(client):
    pid = _project(client)
    rid = _room(client, pid)

    r = _file_card(client, rid)

    assert r.status_code == 422
    assert "产物" in r.json()["message"]
    assert _manifest(client, pid) == []


def test_a_delivery_that_names_both_is_refused(client):
    pid = _project(client)
    rid = _room(client, pid)

    r = _file_card(client, rid, artifact=str(uuid.uuid4()), new_artifact="结题报告")

    assert r.status_code == 422
    assert _manifest(client, pid) == []


def test_two_projects_each_keep_their_own_report(client):
    one, two = _project(client), _project(client)
    _file_card(client, _room(client, one), new_artifact="结题报告")
    _file_card(client, _room(client, two), new_artifact="结题报告")

    assert _manifest(client, one) == [("结题报告", 0)]
    assert _manifest(client, two) == [("结题报告", 0)]
