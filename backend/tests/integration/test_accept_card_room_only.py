"""递卡是房间的事，支线递不出去。

一棵树 = 一个分支 = 一个 PR = 一批活，而**一批**活是整个房间的。所以「这批做完了，
开 PR」是房间说的一句话，不是房间里某一条支线能替它说的。支线各自递卡，等于兄弟们
共同写的那条分支被其中一个人单方面封口——另外几条活还在往上写，PR 里却已经带着
它们半截的样子飞出去了。

这条规则以前只写在人的嘴里，代码一个字没拦，于是三条支线全都递了卡，撞出三种不同
的失败（一次开出空 PR 并被采纳，一次卡在 pending 推不上去，一次 422 说"已有待处理
的验收卡"）。这个文件按**行为**盯住两面：支线递不出去，房间照常递得出去。
"""

import uuid

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
    r = client.post(f"/topics/{room_id}/split", json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _file_card(client, place_id: str, reviewer: str = "alice"):
    return client.post(
        f"/topics/{place_id}/accept-card",
        json={
            "change_subject": _SUBJECT,
            "reviewer_handle": reviewer,
            "routing_reason": "最懂",
        },
    )


def test_a_thread_cannot_file_an_accept_card(client):
    _, room = _room(client)
    thread = _thread(client, room)

    r = _file_card(client, thread)

    assert r.status_code == 422, r.text
    message = r.json()["message"]
    # 光说"不允许"会让分身原地打转。错误必须告诉它下一步敲什么。
    assert "cheese conclude" in message


def test_the_room_itself_still_files_cards(client):
    """另一面：上面那条不能是把所有人一起拦掉换来的。"""
    _, room = _room(client)

    r = _file_card(client, room)

    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "pending"


def test_a_thread_being_refused_does_not_use_up_the_room_s_one_card(client):
    """拒绝必须发生在写任何东西之前。

    一棵树同时只允许一张未决的卡。如果支线那次被拒之前已经把卡建进去了，房间就再也
    递不出自己的那张——一条支线能就此把整个房间的交付卡死。
    """
    _, room = _room(client)
    thread = _thread(client, room)

    assert _file_card(client, thread).status_code == 422
    assert client.get(f"/topics/{thread}/accept-card").json()["data"]["total"] == 0

    r = _file_card(client, room)
    assert r.status_code == 200, r.text


def test_a_thread_is_refused_before_the_subject_is_even_checked(client):
    """连主题都不用带——它递不出卡这件事和它怎么写无关。

    盯的是顺序：先说"这不该由你来做"，而不是先挑一遍它填的表格。挑表格会让分身以为
    改一改还能递，于是它把主题改对、再撞一次同一堵墙。
    """
    _, room = _room(client)
    thread = _thread(client, room)

    r = client.post(
        f"/topics/{thread}/accept-card",
        json={"change_subject": "", "reviewer_handle": "alice"},
    )

    assert r.status_code == 422, r.text
    assert "cheese conclude" in r.json()["message"]


def test_an_id_that_names_nothing_is_still_a_404(client):
    """拒绝支线不能顺手把"不存在"也说成"你不是房间"。"""
    r = _file_card(client, str(uuid.uuid4()))
    assert r.status_code == 404, r.text
