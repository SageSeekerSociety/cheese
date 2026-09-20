"""产物清单 —— 交付时点名，采纳把它记成一版 (#1085 结论三、五)。

清单没有「新建」入口，所以这里每一项都是从递卡进来的：一个项目做出来的东西是交付
出来的，不是先在某处登记的。
"""

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


def _file_card(client, room_id: str, *, artifact: str | None = "结题报告"):
    body = {
        "change_subject": "chore(test): file an accept card",
        "reviewer_handle": "alice",
    }
    if artifact is not None:
        body["artifact"] = artifact
    return client.post(
        f"/topics/{room_id}/tasks/{delivery_task_id(client, room_id)}/accept-card",
        headers=delivery_headers(client, room_id),
        json=body,
    )


def _manifest(client, project_id: str) -> list[tuple[str, int]]:
    r = client.get(f"/projects/{project_id}/artifacts")
    assert r.status_code == 200, r.text
    return [(a["name"], a["version"]) for a in r.json()["data"]["data"]]


def _accept(client, card_id: str):
    return client.post(
        f"/accept-cards/{card_id}/accept",
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


def test_a_delivery_names_what_it_updates_and_the_manifest_grows_it(client):
    pid = _project(client)
    rid = _room(client, pid)

    r = _file_card(client, rid, artifact="结题报告")

    assert r.status_code == 200
    assert r.json()["data"]["artifact"] == {"name": "结题报告", "version": 0}
    assert _manifest(client, pid) == [("结题报告", 0)]


def test_a_name_the_project_never_delivered_before_is_said_out_loud(client):
    pid = _project(client)
    rid = _room(client, pid)

    _file_card(client, rid, artifact="结题报告")

    # 新建是会长出垃圾的那一下，所以房间里当场有一行说它是新的。
    assert any("新建了产物《结题报告》" in line for line in _lines(client, rid))


def test_a_new_name_is_shown_next_to_what_the_project_already_has(client):
    """判断「这是不是刚才那一项换了个说法」要两个名字摆在一起。"""
    pid = _project(client)
    first = _room(client, pid, "第一轮")
    _file_card(client, first, artifact="结题报告")
    second = _room(client, pid, "第二轮")

    _file_card(client, second, artifact="报告")

    detail = _notice_detail(client, second, "新建了产物《报告》")
    assert "《结题报告》" in detail
    assert "《报告》" in detail


def test_the_same_name_is_the_same_artifact_not_a_second_entry(client):
    pid = _project(client)
    first = _room(client, pid, "第一轮")
    second = _room(client, pid, "第二轮")

    _file_card(client, first, artifact="结题报告")
    _file_card(client, second, artifact="结题报告")

    assert _manifest(client, pid) == [("结题报告", 0)]
    # 第二次不是新建，所以没有第二条「新建」。
    assert not any("新建了产物" in line for line in _lines(client, second))


def test_a_version_is_a_delivery_that_landed(client):
    pid = _project(client)
    rid = _room(client, pid)
    cid = _file_card(client, rid, artifact="结题报告").json()["data"]["id"]

    # 递卡不是一版：这张卡还可能被驳回。
    assert _manifest(client, pid) == [("结题报告", 0)]

    assert _accept(client, cid).status_code == 200

    assert _manifest(client, pid) == [("结题报告", 1)]


def test_a_revoked_delivery_is_not_a_version_any_more(client):
    pid = _project(client)
    rid = _room(client, pid)
    cid = _file_card(client, rid, artifact="结题报告").json()["data"]["id"]
    _accept(client, cid)

    r = client.post(
        f"/accept-cards/{cid}/revoke",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200

    # 撤回采纳之后那一版不在了，清单上的项还在 —— 它被声明过。
    assert _manifest(client, pid) == [("结题报告", 0)]


def test_a_delivery_that_names_nothing_is_refused(client):
    pid = _project(client)
    rid = _room(client, pid)

    r = _file_card(client, rid, artifact=None)

    assert r.status_code == 422
    assert "产物" in r.json()["message"]
    assert _manifest(client, pid) == []


def test_two_projects_each_keep_their_own_report(client):
    one, two = _project(client), _project(client)
    _file_card(client, _room(client, one), artifact="结题报告")
    _file_card(client, _room(client, two), artifact="结题报告")

    assert _manifest(client, one) == [("结题报告", 0)]
    assert _manifest(client, two) == [("结题报告", 0)]
