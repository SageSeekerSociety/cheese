"""房间开一条活、分身交回结论、验完货把它收掉——这条链上的端点。

`split` 写下那一行就停，并把这张卡的**线程标识**交出来；房间在自己已有的会话里起
一个分身、把标识带上，平台就能把它干的事记到这张卡上（结论 43）——**没有一个
「认领」的调用**：分身的 id 在容器里才诞生，而标识两边事先都知道，所以谁也不用把
什么报回来。分身每一次收工把那句话写在卡上；房间读过交回来的东西、把改动并进来
之后，用 `close` 收卡。

Every refusal here is one somebody would otherwise hit silently: an id that
names another room's work, a thread that already finished.
"""

import uuid

from tests.conftest import wait_work_idle as _wait_work_idle
from tests.integration.conftest import chat_ws_url, post_project, session_token


def _bearer(handle: str) -> dict:
    return {"Authorization": f"Bearer {session_token(handle)}"}


def _room(client, owner: str = "alice") -> tuple[str, str]:
    p = post_project(client, json={"name": "P", "owner_handle": owner}).json()["data"]
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


def test_opening_work_hands_out_the_label_its_events_will_carry(client):
    """开卡就拿到了线程标识——起子 agent 时带上它，它干的事就落在这张卡上。

    这是平台在派活之前就能说出口的那一句：分身的 id 要到容器里才诞生，而这个标识
    两边事先都知道，所以归属不再需要谁回头报一次（结论 43）。
    """
    _, room_id = _room(client)
    task = _split(client, room_id)

    assert task["thread_label"], task
    assert task["id"].replace("-", "") in task["thread_label"]

    listed = client.get(f"/topics/{room_id}/tasks", headers=_bearer("alice")).json()[
        "data"
    ]["data"]
    assert [t["thread_label"] for t in listed] == [task["thread_label"]]


def test_a_card_cannot_conclude_itself(client):
    """收卡是房间的事：活自己说了不算，它就是被评价的那一方。

    这条路的第一段是房间的地址，而一张卡不是地点，所以它连入口都走不到 —— 404，
    不是一条判出来的拒绝。
    """
    _, room_id = _room(client)
    task = _split(client, room_id)

    r = client.post(
        f"/topics/{task['id']}/tasks/{task['id']}/close",
        json={"conclusion": "做完了"},
        headers=_bearer("alice"),
    )
    assert r.status_code == 404, r.text


def test_the_room_closing_a_card_is_what_ends_the_work(client):
    """收卡:状态翻 closed、盖上收卡时刻,结论留在卡上。"""
    _, room_id = _room(client)
    task = _split(client, room_id)

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
    with client.websocket_connect(chat_ws_url(room_id, "alice")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 你好"})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass
    _wait_work_idle()
    stub_hooks.spawns(uuid.UUID(room_id), thread_label=task["thread_label"])
    _reports_back(
        stub_hooks,
        uuid.UUID(room_id),
        agent_id="worker-1",
        summary="索引加好了，慢查询从 2.1s 降到 40ms",
    )
    _wait_work_idle()

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


def _reports_back(stub, topic_id, *, agent_id: str, summary: str) -> None:
    """一个分身报「做完了」：它那条任务的 task_notification，收尾话在 summary 里。"""
    stub.record(
        topic_id,
        type="system",
        subtype="task_notification",
        task_id=agent_id,
        tool_use_id="call-1",
        status="completed",
        summary=summary,
    )


def _shown(client, room_id: str, task_id: str) -> dict:
    listed = client.get(f"/topics/{room_id}/tasks", headers=_bearer("alice")).json()[
        "data"
    ]["data"]
    return next(t["presentation"] for t in listed if t["id"] == task_id)


def test_a_card_nobody_has_started_on_is_idle_not_out_of_contact(client):
    """还没分身开工的活是「待开工」，不是「失联」。

    开卡只留下分支、卡和负责人；谁在做要等平台看见一条带着这张卡线程标识的开工
    事件才知道（下一条）。屏幕没了、分身跟着没了那一格在
    `tests/unit/test_presentation.py`。
    """
    _, room_id = _room(client)
    task = _split(client, room_id)

    assert _shown(client, room_id, task["id"])["display_status"] == "待开工", (
        "还没人做的活不是失联,是没人做"
    )


def test_a_worker_reporting_in_is_not_the_work_finishing(client, stub_hooks):
    """一个分身可以报好几次完成（把长命令丢进自己的后台再停下来等也算一次），而且
    还会飘来来路不明的完成通知。所以一条分身的完成通知落在这条活的时间线上之后，
    看板绝不能把它翻成「已收工」—— 只有房间验过货、落了结论才算。"""
    _, room_id = _room(client)
    task = _split(client, room_id)
    # 一轮普通的轮次，房间因此有了一个活着的会话（分身的记录才有地方来）。
    with client.websocket_connect(chat_ws_url(room_id, "alice")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 你好"})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass
    _wait_work_idle()
    # 分身开工：标识写在起它的那次调用里，平台因此知道是谁在做。
    stub_hooks.spawns(uuid.UUID(room_id), thread_label=task["thread_label"])
    _wait_work_idle()
    assert _shown(client, room_id, task["id"])["display_status"] == "运行中"

    _reports_back(
        stub_hooks,
        uuid.UUID(room_id),
        agent_id="worker-1",
        summary="我这边跑完了",
    )
    _wait_work_idle()

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


def test_the_last_stop_wins_and_an_unlabelled_worker_writes_nothing(client, stub_hooks):
    """结论取最后一条，且只认标识指向这张卡的。

    Both halves are measured behaviour, not preference. A worker stops more than
    once — parking a long command in its own background reads as finishing —
    so an early "跑起来了" must not stand as the answer. And after the session's
    own result, a task notification arrives from something inside Claude Code,
    with a fragment of a prompt where the closing message should be — a worker
    this session never spawned. Letting that land would put a stranger's
    half-sentence on somebody's card.
    """
    _, room_id = _room(client)
    task = _split(client, room_id)
    with client.websocket_connect(chat_ws_url(room_id, "alice")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 你好"})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass
    _wait_work_idle()

    stub_hooks.spawns(uuid.UUID(room_id), thread_label=task["thread_label"])
    for message in ("测试跑起来了，我等它", "全绿，3617 passed"):
        _reports_back(
            stub_hooks,
            uuid.UUID(room_id),
            agent_id="worker-1",
            summary=message,
        )
        _wait_work_idle()
    _reports_back(
        stub_hooks,
        uuid.UUID(room_id),
        agent_id="a-stranger",
        summary="…请用一句话概括",
    )
    _wait_work_idle()

    listed = client.get(f"/topics/{room_id}/tasks", headers=_bearer("alice")).json()[
        "data"
    ]["data"]
    assert [t["conclusion"] for t in listed] == ["全绿，3617 passed"]
