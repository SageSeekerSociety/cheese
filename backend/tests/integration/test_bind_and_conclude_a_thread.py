"""房间开一条活、认领一个分身、验完货把它收掉——这条链上的三个端点。

Under 任务=分身 the platform stops raising a container per thread. `split` writes
the row and stops; the room spawns a worker in the session it already has and
says which one with `bind`; the worker's own stops write themselves onto the
card as its conclusion; and when the room has read what came back and folded the
changes in, it closes the card with `conclude`.

Every refusal here is one somebody would otherwise hit silently: an id that
names another room's work, a thread that already finished, a worker already
doing something else. None of those fail loudly on their own — they just
re-address a running worker's events and leave the first thread wondering where
its tool calls went.
"""

import uuid

from tests.conftest import wait_work_idle as _wait_work_idle
from tests.integration.conftest import chat_ws_url, session_token


def _bearer(handle: str) -> dict:
    return {"Authorization": f"Bearer {session_token(handle)}"}


def _room(client, owner: str = "alice") -> tuple[str, str]:
    p = client.post("/projects", json={"name": "P", "owner_handle": owner}).json()[
        "data"
    ]
    return p["id"], p["root_topic_id"]


def _split(client, room_id: str, owner: str = "alice", title: str = "一条活") -> dict:
    r = client.post(
        f"/topics/{room_id}/split",
        json=dict(reviewer_handle="alice", **{"title": title, "brief": "干这个"}),
        headers=_bearer(owner),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_a_fresh_thread_has_nobody_on_it(client):
    """split 只建行,不起人——「没人做」是正常状态,不是没派成功。"""
    _, room_id = _room(client)
    task = _split(client, room_id)

    assert task["subagent_id"] is None
    # 排队机制随着每条活一个容器一起走了:没有容器可排。
    assert "queued" not in task
    assert "slots_held_by" not in task


def test_binding_says_which_worker_is_on_it(client):
    _, room_id = _room(client)
    task = _split(client, room_id)

    r = client.post(
        f"/topics/{room_id}/tasks/{task['id']}/bind",
        json={"agent_id": "worker-1"},
        headers=_bearer("alice"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["subagent_id"] == "worker-1"

    listed = client.get(f"/topics/{room_id}/tasks", headers=_bearer("alice")).json()[
        "data"
    ]["data"]
    assert [t["subagent_id"] for t in listed] == ["worker-1"]


def test_binding_the_same_worker_twice_is_refused(client):
    """一个分身同时只做一条活。

    Not a bookkeeping nicety: rebinding would not move the work, it would
    re-address a running worker's events, and the first thread would stop
    receiving its own tool calls without anything saying so.
    """
    _, room_id = _room(client)
    first = _split(client, room_id, title="活一")
    second = _split(client, room_id, title="活二")
    client.post(
        f"/topics/{room_id}/tasks/{first['id']}/bind",
        json={"agent_id": "worker-1"},
        headers=_bearer("alice"),
    )

    r = client.post(
        f"/topics/{room_id}/tasks/{second['id']}/bind",
        json={"agent_id": "worker-1"},
        headers=_bearer("alice"),
    )
    assert r.status_code == 409
    assert "活一" in r.text


def test_rebinding_the_same_thread_to_the_same_worker_is_fine(client):
    """重发一次 bind 不该报错——同一条活、同一个分身,答案没变。"""
    _, room_id = _room(client)
    task = _split(client, room_id)
    for _ in range(2):
        r = client.post(
            f"/topics/{room_id}/tasks/{task['id']}/bind",
            json={"agent_id": "worker-1"},
            headers=_bearer("alice"),
        )
        assert r.status_code == 200, r.text


def test_another_rooms_thread_cannot_be_bound_here(client):
    _, room_a = _room(client)
    _, room_b = _room(client)
    task = _split(client, room_b)

    r = client.post(
        f"/topics/{room_a}/tasks/{task['id']}/bind",
        json={"agent_id": "worker-1"},
        headers=_bearer("alice"),
    )
    assert r.status_code == 404


def test_a_finished_thread_cannot_be_bound(client):
    """收了的活不接新分身——接了,它的事件会被一条谁也不看的线吸走。"""
    _, room_id = _room(client)
    task = _split(client, room_id)
    closed = client.post(
        f"/topics/{room_id}/tasks/{task['id']}/close",
        json={"conclusion": "做完了"},
        headers=_bearer("alice"),
    )
    assert closed.status_code == 200, closed.text

    r = client.post(
        f"/topics/{room_id}/tasks/{task['id']}/bind",
        json={"agent_id": "worker-2"},
        headers=_bearer("alice"),
    )
    assert r.status_code == 409


def test_an_outsider_cannot_bind(client):
    _, room_id = _room(client)
    task = _split(client, room_id)

    r = client.post(
        f"/topics/{room_id}/tasks/{task['id']}/bind",
        json={"agent_id": "worker-1"},
        headers=_bearer("mallory"),
    )
    assert r.status_code == 403


def test_a_card_cannot_bind_or_conclude_itself(client):
    """认领和收卡都是房间的事:活自己说了不算,它就是被评价的那一方。

    这两条路的第一段是房间的地址,而一张卡不是地点,所以它连入口都走不到 —— 404,
    不是一条判出来的拒绝。
    """
    _, room_id = _room(client)
    task = _split(client, room_id)

    for path, body in (
        (f"/topics/{task['id']}/tasks/{task['id']}/bind", {"agent_id": "w"}),
        (f"/topics/{task['id']}/tasks/{task['id']}/close", {"conclusion": "做完了"}),
    ):
        r = client.post(path, json=body, headers=_bearer("alice"))
        assert r.status_code == 404, (path, r.status_code, r.text)


def test_the_room_closing_a_card_is_what_ends_the_work(client):
    """收卡:状态翻 closed、盖上收卡时刻,结论留在卡上。"""
    _, room_id = _room(client)
    task = _split(client, room_id)
    client.post(
        f"/topics/{room_id}/tasks/{task['id']}/bind",
        json={"agent_id": "worker-1"},
        headers=_bearer("alice"),
    )

    r = client.post(
        f"/topics/{room_id}/tasks/{task['id']}/close",
        json={"conclusion": "分页改成 cursor，旧接口没动"},
        headers=_bearer("alice"),
    )
    assert r.status_code == 200, r.text
    closed = r.json()["data"]
    assert closed["status"] == "closed"
    assert closed["closed_at"] is not None
    assert closed["conclusion"] == "分页改成 cursor，旧接口没动"

    listed = client.get(f"/topics/{room_id}/tasks", headers=_bearer("alice")).json()[
        "data"
    ]["data"]
    assert [t["status"] for t in listed] == ["closed"]


def test_closing_without_a_word_keeps_what_the_worker_handed_back(client, stub_hooks):
    """结论已经在卡上了(分身停下时平台写的),收卡不用房间抄一遍。"""
    _, room_id = _room(client)
    task = _split(client, room_id)
    client.post(
        f"/topics/{room_id}/tasks/{task['id']}/bind",
        json={"agent_id": "worker-1"},
        headers=_bearer("alice"),
    )
    with client.websocket_connect(chat_ws_url(room_id, "alice")) as ws:
        ws.send_json({"type": "message", "content": "你好", "summon": True})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass
    _wait_work_idle()
    stub_hooks.hook(
        uuid.UUID(room_id),
        hook_event_name="SubagentStop",
        agent_id="worker-1",
        last_assistant_message="索引加好了，慢查询从 2.1s 降到 40ms",
    )
    _pump(client, room_id)

    r = client.post(
        f"/topics/{room_id}/tasks/{task['id']}/close",
        json={},
        headers=_bearer("alice"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["conclusion"] == "索引加好了，慢查询从 2.1s 降到 40ms"


def test_closing_a_thread_of_another_room_is_refused(client):
    _, room_a = _room(client)
    _, room_b = _room(client)
    task = _split(client, room_b)

    r = client.post(
        f"/topics/{room_a}/tasks/{task['id']}/close",
        json={"conclusion": "做完了"},
        headers=_bearer("alice"),
    )
    assert r.status_code == 404


def test_closing_something_that_is_not_a_thread_is_refused(client):
    _, room_id = _room(client)
    r = client.post(
        f"/topics/{room_id}/tasks/{uuid.uuid4()}/close",
        json={"conclusion": "做完了"},
        headers=_bearer("alice"),
    )
    assert r.status_code == 404


# —— 看板上的这条活 ——————————————————————————————————————————————


def _shown(client, room_id: str, task_id: str) -> dict:
    listed = client.get(f"/topics/{room_id}/tasks", headers=_bearer("alice")).json()[
        "data"
    ]["data"]
    return next(t["presentation"] for t in listed if t["id"] == task_id)


def _pump(client, room_id: str, rounds: int = 20) -> None:
    """把 TestClient 那条事件循环叫醒几次。

    钩子是从测试这条线程塞进它队列里的，塞的时候唤不醒它（跨线程 `put_nowait` 叫
    不动等在那儿的 waiter）；它下一次醒来是因为有请求进来。
    """
    import time

    for _ in range(rounds):
        client.get(f"/topics/{room_id}", headers=_bearer("alice"))
        time.sleep(0.02)


def test_a_thread_whose_room_has_no_screen_says_it_is_out_of_contact(client):
    """分身住在房间的会话里 —— 屏幕没了它一定也没了，而它不会来说一声。看板不问，
    这条活就永远转圈。"""
    _, room_id = _room(client)
    task = _split(client, room_id)
    assert _shown(client, room_id, task["id"])["display_status"] == "空闲", (
        "还没人做的活不是失联,是没人做"
    )

    client.post(
        f"/topics/{room_id}/tasks/{task['id']}/bind",
        json={"agent_id": "worker-1"},
        headers=_bearer("alice"),
    )
    assert _shown(client, room_id, task["id"])["display_status"] == "失联"


def test_a_worker_reporting_in_is_not_the_work_finishing(client, stub_hooks):
    """一个分身可以报好几次完成（把长命令丢进自己的后台再停下来等也算一次），而且
    还会飘来来路不明的完成通知。所以一条 SubagentStop 落在这条活的时间线上之后，
    看板绝不能把它翻成「已收工」—— 只有房间验过货、落了结论才算。"""
    _, room_id = _room(client)
    task = _split(client, room_id)
    client.post(
        f"/topics/{room_id}/tasks/{task['id']}/bind",
        json={"agent_id": "worker-1"},
        headers=_bearer("alice"),
    )
    # 一轮普通的轮次，房间因此有了一块活着的屏幕（也才有钩子可以推）。
    with client.websocket_connect(chat_ws_url(room_id, "alice")) as ws:
        ws.send_json({"type": "message", "content": "你好", "summon": True})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass
    _wait_work_idle()
    assert _shown(client, room_id, task["id"])["display_status"] == "运行中"

    stub_hooks.hook(
        uuid.UUID(room_id),
        hook_event_name="SubagentStop",
        agent_id="worker-1",
        last_assistant_message="我这边跑完了",
    )
    _pump(client, room_id)

    blocks = client.get(
        f"/topics/{room_id}/tasks/{task['id']}", headers=_bearer("alice")
    ).json()["data"]["blocks"]
    assert any("我这边跑完了" in b["content"] for b in blocks), (
        "分身的收尾话没落到这条活上"
    )

    shown = _shown(client, room_id, task["id"])
    assert shown["column"] == "building", f"报了一次完成就被当成干完了：{shown}"
    assert shown["display_status"] != "已收工"
    listed = client.get(f"/topics/{room_id}/tasks", headers=_bearer("alice")).json()[
        "data"
    ]["data"]
    assert [t["status"] for t in listed] == ["open"]


def test_the_last_stop_wins_and_a_worker_nobody_bound_writes_nothing(
    client, stub_hooks
):
    """结论取最后一条,且只认绑过的 id。

    Both halves are measured behaviour, not preference. A worker stops more than
    once — parking a long command in its own background reads as finishing —
    so an early "跑起来了" must not stand as the answer. And after the session's
    own Stop, a SubagentStop arrives from something inside Claude Code: an id
    nobody bound, an empty type, a fragment of a prompt where the closing
    message should be. Letting that land would put a stranger's half-sentence on
    somebody's card.
    """
    _, room_id = _room(client)
    task = _split(client, room_id)
    client.post(
        f"/topics/{room_id}/tasks/{task['id']}/bind",
        json={"agent_id": "worker-1"},
        headers=_bearer("alice"),
    )
    with client.websocket_connect(chat_ws_url(room_id, "alice")) as ws:
        ws.send_json({"type": "message", "content": "你好", "summon": True})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass
    _wait_work_idle()

    for message in ("测试跑起来了，我等它", "全绿，3617 passed"):
        stub_hooks.hook(
            uuid.UUID(room_id),
            hook_event_name="SubagentStop",
            agent_id="worker-1",
            last_assistant_message=message,
        )
        _pump(client, room_id)
    stub_hooks.hook(
        uuid.UUID(room_id),
        hook_event_name="SubagentStop",
        agent_id="nobody-bound-this-one",
        last_assistant_message="…请用一句话概括",
    )
    _pump(client, room_id)

    listed = client.get(f"/topics/{room_id}/tasks", headers=_bearer("alice")).json()[
        "data"
    ]["data"]
    assert [t["conclusion"] for t in listed] == ["全绿，3617 passed"]
