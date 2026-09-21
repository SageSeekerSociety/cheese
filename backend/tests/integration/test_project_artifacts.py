"""产物清单的一生 —— 交付创建它，交付给它加版本，没交付成的不留在清单上 (#1085)。

**这里每一张卡交出去的都是一个地址**，因为「声明动的是哪一项」只在交文件、交地址的
交付上存在：
交出去一次合并的，交的是项目那个仓库，谁都不用声明 —— 那一半在
`test_artifact_is_the_repository.py`。

沿用和新建是两个动作，所以这里每个错法都要有自己的一句话：沿用一个清单上没有的名
字、新建一个已经在清单上的名字、两个都给、两个都不给。写错名字本身不会报错（`报
告` 和 `结题报告` 都合法），把它变成一次当场的报错正是这两个动作存在的理由。
"""

import uuid

import pytest

from app.core.sandbox_auth import mint_scoped_token
from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import session_auth_headers
from tests.integration.test_accept_pr import (
    _give_card_a_pr,
    _rendered_head,
    app_world,  # noqa: F401
)


@pytest.fixture(autouse=True)
def remote_delivery(client, request):
    client.artifact_forge = request.getfixturevalue("app_world")


def _project(client) -> str:
    r = client.post("/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _room(client, project_id: str, title: str = "做一个东西") -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": title})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _file_card(client, room_id: str, **artifact):
    """递一张**交出去一个地址**的卡。`artifact` / `new_artifact` 原样传下去，包括
    一个都不给。

    交的是地址，因为声明产物这个动作只在交文件、交地址的交付上存在：交出去一次合
    并的，交的是项目那个仓库，平台自己认得出是哪一项（见
    `test_artifact_is_the_repository.py`）。地址不用在机器上放一份文件，这个文件
    问的又不是交付物本身的事。
    """
    body = {
        "change_subject": "chore(test): file an accept card",
        "reviewer_handle": "alice",
        "deliver_url": "https://example.com/交出去的那一份",
        **({"about": "交给甲方的那一份"} if artifact.get("new_artifact") else {}),
        **artifact,
    }
    response = client.post(
        f"/topics/{room_id}/tasks/{delivery_task_id(client, room_id)}/accept-card",
        headers=delivery_headers(client, room_id),
        json=body,
    )
    if response.status_code == 200:
        _give_card_a_pr(
            client,
            client.artifact_forge,
            room_id,
            response.json()["data"]["id"],
            number=100 + len(client.artifact_forge["fake"].prs),
        )
    return response


def _manifest_rows(client, project_id: str) -> list[dict]:
    r = client.get(f"/projects/{project_id}/artifacts")
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _manifest(client, project_id: str) -> list[tuple[str, int]]:
    return [(a["name"], a["version"]) for a in _manifest_rows(client, project_id)]


def _decide(client, card_id: str, action: str):
    return client.post(
        f"/accept-cards/{card_id}/{action}",
        json={"decided_by": "alice", "head_sha": _rendered_head(client, card_id)},
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
    # 卡上写的是这张卡自己那一版：采纳它，《结题报告》就有了第 1 版。清单上那一行
    # 仍然是 0 —— 那说的是「这一项已经交出去过几次」，现在还是零次。两个数答的是
    # 两个问题，卡面要答的是人正在定的那个。
    assert r.json()["data"]["artifact"]["version"] == 1
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


# --- 人的动作：改名、合并、删除 ---------------------------------------------


def test_renaming_keeps_the_versions_already_counted(client):
    """名字起错了的正解是改名 —— 卡指着的是行的 id，所以版本一个不丢。"""
    pid = _project(client)
    rid = _room(client, pid)
    cid = _file_card(client, rid, new_artifact="报告").json()["data"]["id"]
    _decide(client, cid, "accept")
    aid = _manifest_rows(client, pid)[0]["id"]

    r = client.patch(f"/projects/{pid}/artifacts/{aid}", json={"name": "结题报告"})

    assert r.status_code == 200, r.text
    assert _manifest(client, pid) == [("结题报告", 1)]


def test_renaming_onto_another_item_is_refused_and_points_at_merging(client):
    pid = _project(client)
    first = _room(client, pid, "第一轮")
    second = _room(client, pid, "第二轮")
    _file_card(client, first, new_artifact="结题报告")
    _file_card(client, second, new_artifact="项目官网")
    site = next(a for a in _manifest_rows(client, pid) if a["name"] == "项目官网")

    r = client.patch(
        f"/projects/{pid}/artifacts/{site['id']}", json={"name": "结题报告"}
    )

    assert r.status_code == 422
    assert "合并" in r.json()["message"]


def test_merging_adds_the_versions_of_both(client):
    """两项其实是同一个东西：交付记在卡上，所以合并之后版本是两边加起来。"""
    pid = _project(client)
    first = _room(client, pid, "第一轮")
    second = _room(client, pid, "第二轮")
    one = _file_card(client, first, new_artifact="报告").json()["data"]["id"]
    _decide(client, one, "accept")
    two = _file_card(client, second, new_artifact="结题报告").json()["data"]["id"]
    _decide(client, two, "accept")
    rows = {a["name"]: a["id"] for a in _manifest_rows(client, pid)}

    r = client.post(
        f"/projects/{pid}/artifacts/{rows['报告']}/merge",
        json={"into": rows["结题报告"]},
    )

    assert r.status_code == 200, r.text
    assert _manifest(client, pid) == [("结题报告", 2)]


def test_deleting_takes_the_item_off_the_list_and_leaves_the_card(client):
    pid = _project(client)
    rid = _room(client, pid)
    cid = _file_card(client, rid, new_artifact="结题报告").json()["data"]["id"]
    _decide(client, cid, "accept")
    aid = _manifest_rows(client, pid)[0]["id"]

    r = client.delete(f"/projects/{pid}/artifacts/{aid}")

    assert r.status_code == 200, r.text
    assert _manifest(client, pid) == []
    # 那次交付确实发生过，卡还在，只是不再指向任何一项。
    card = client.get(f"/topics/{rid}/accept-card").json()["data"]["data"][0]
    assert card["status"] == "accepted"
    assert card["artifact"] is None


def test_an_item_from_another_project_is_not_on_this_list(client):
    mine, theirs = _project(client), _project(client)
    _file_card(client, _room(client, theirs), new_artifact="结题报告")
    theirs_id = _manifest_rows(client, theirs)[0]["id"]

    r = client.delete(f"/projects/{mine}/artifacts/{theirs_id}")

    assert r.status_code == 404
    assert _manifest(client, theirs) == [("结题报告", 0)]


def test_the_agent_cannot_change_the_list(client):
    """芝士 只能在交付时声明；改名、合并、删除是人的判断。"""
    pid = _project(client)
    rid = _room(client, pid)
    _file_card(client, rid, new_artifact="结题报告")
    aid = _manifest_rows(client, pid)[0]["id"]

    client.headers.pop("Authorization", None)
    client.cookies.clear()
    agent = {
        "X-Cheese-Token": mint_scoped_token(project_id=pid, topic_id=rid, ttl_s=3600)
    }

    assert (
        client.patch(
            f"/projects/{pid}/artifacts/{aid}", json={"name": "报告"}, headers=agent
        ).status_code
        == 403
    )
    assert (
        client.delete(f"/projects/{pid}/artifacts/{aid}", headers=agent).status_code
        == 403
    )


# --- 那一句话：说清这是什么东西，下一次交付才判断得了 -----------------------


def _about(client, project_id: str, name: str) -> str:
    row = next(a for a in _manifest_rows(client, project_id) if a["name"] == name)
    return row["about"]


def test_a_new_item_without_a_sentence_is_refused(client):
    """清单上只有名字的话，下一次交付又只能看着名字猜。"""
    pid = _project(client)
    rid = _room(client, pid)

    r = _file_card(client, rid, new_artifact="结题报告", about="")

    assert r.status_code == 422
    assert _manifest(client, pid) == []


def test_the_sentence_shows_up_next_to_the_name(client):
    pid = _project(client)
    rid = _room(client, pid)

    _file_card(client, rid, new_artifact="结题报告", about="交给甲方的最终报告")

    assert _about(client, pid, "结题报告") == "交给甲方的最终报告"


def test_copying_the_change_subject_into_the_sentence_is_refused(client):
    """按改动标题写，正是清单长成一份改动列表的那条老路。"""
    pid = _project(client)
    rid = _room(client, pid)

    r = _file_card(
        client,
        rid,
        new_artifact="结题报告",
        about="chore(test): file an accept card",
    )

    assert r.status_code == 422
    assert _manifest(client, pid) == []


def test_a_sentence_that_runs_long_is_refused(client):
    pid = _project(client)
    rid = _room(client, pid)

    r = _file_card(client, rid, new_artifact="结题报告", about="报" * 200)

    assert r.status_code == 422
    assert _manifest(client, pid) == []


def test_delivering_again_may_leave_the_sentence_alone(client):
    """写对了的那句话说的是这样东西本身，交一版新的不会让它变得不对。"""
    pid = _project(client)
    first = _room(client, pid, "第一轮")
    second = _room(client, pid, "第二轮")
    declared = _file_card(
        client, first, new_artifact="结题报告", about="交给甲方的最终报告"
    ).json()["data"]

    _file_card(client, second, artifact=declared["artifact"]["id"])

    assert _about(client, pid, "结题报告") == "交给甲方的最终报告"


def test_delivering_again_may_replace_the_sentence(client):
    """要改的那一种情况是这东西真的变成了另一样东西。"""
    pid = _project(client)
    first = _room(client, pid, "第一轮")
    second = _room(client, pid, "第二轮")
    declared = _file_card(
        client, first, new_artifact="结题报告", about="交给甲方的最终报告"
    ).json()["data"]

    _file_card(
        client,
        second,
        artifact=declared["artifact"]["id"],
        about="交给评审组的最终报告",
    )

    assert _about(client, pid, "结题报告") == "交给评审组的最终报告"
