"""递卡是房间的事，一张卡递不出去。

一棵树 = 一个分支 = 一个 PR = 一批活，而**一批**活是整个房间的。所以「这批做完了，
开 PR」是房间说的一句话，不是房间里某一件活能替它说的。每件活各自递卡，等于兄弟们
共同写的那条分支被其中一个单方面封口——另外几条活还在往上写，PR 里却已经带着它们
半截的样子飞出去了。

这条规则以前只写在人的嘴里，代码一个字没拦，于是三条活全都递了卡，撞出三种不同的
失败（一次开出空 PR 并被采纳，一次卡在 pending 推不上去，一次 422 说"已有待处理的
验收卡"）。**现在它由地址空间保证**：一张卡不是地点，`/topics/{卡的 id}/accept-card`
名下没有话题，所以那条路根本不通——而不是通了以后再判一次。
"""

import uuid

from tests.delivery import delivery_headers, delivery_task_id

_SUBJECT = "chore(test): file an accept card"


def _room(client) -> tuple[str, str]:
    pid = client.post("/projects", json={"name": "P", "owner_handle": "alice"}).json()[
        "data"
    ]["id"]
    rid = client.post(
        "/topics",
        json={"project_id": pid, "title": "房间", "created_by": "alice"},
    ).json()["data"]["id"]
    return pid, rid


def _thread(client, room_id: str, title: str = "一件活") -> str:
    r = client.post(
        f"/topics/{room_id}/split",
        json=dict(reviewer_handle="alice", **{"title": title}),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _file_card(client, place_id: str, reviewer: str = "alice"):
    return client.post(
        f"/topics/{place_id}/tasks/{delivery_task_id(client, place_id)}/accept-card",
        headers=delivery_headers(client, place_id),
        json={
            "change_subject": _SUBJECT,
            "reviewer_handle": reviewer,
            "routing_reason": "最懂",
        },
    )


def test_a_card_cannot_file_an_accept_card(client):
    """404，而不是一条判出来的拒绝：那个 id 名下没有地点。"""
    _, room = _room(client)
    card = _thread(client, room)

    assert _file_card(client, card).status_code == 404


def test_the_room_itself_still_files_cards(client):
    """另一面：上面那条不能是把所有人一起拦掉换来的。"""
    _, room = _room(client)

    r = _file_card(client, room)

    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "pending"


def test_a_refused_card_does_not_use_up_the_room_s_one_card(client):
    """走不通必须发生在写任何东西之前。

    一棵树同时只允许一张未决的卡。如果那次不通之前已经把卡建进去了，房间就再也
    递不出自己的那张——一件活能就此把整个房间的交付卡死。
    """
    _, room = _room(client)
    card = _thread(client, room)

    assert _file_card(client, card).status_code == 404
    assert client.get(f"/topics/{room}/accept-card").json()["data"]["total"] == 0

    r = _file_card(client, room)
    assert r.status_code == 200, r.text


def test_an_id_that_names_nothing_is_a_404_too(client):
    """一张卡和一个不存在的 id 得到同一句话，因为对这条路来说它们是同一件事。"""
    r = _file_card(client, str(uuid.uuid4()))
    assert r.status_code == 404, r.text
