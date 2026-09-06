"""对着一条活说话，说给的是它的房间。

一条活没有自己的会话：做它的分身活在房间的会话里，人够不着它。所以每一条通往活的
话都只有一条路——落在活的时间线上（人看的是那儿），把房间叫醒去转达（能动手的只有
它）。这个文件钉住那条路的两个方向：人从界面上留言，以及房间用 `cheese tell` 回话。

反过来的证据一样重要：**没有任何一条路会拿活的 id 去开一轮**。开一轮就是起一块屏幕，
起屏幕就是起一整个容器——正是「一条活 = 房间会话里的一个分身」拆掉的东西。
"""

from tests.conftest import wait_work_idle as _wait_work_idle
from tests.integration.conftest import chat_ws_url


def _project(client) -> dict:
    return client.post(
        "/projects", json={"name": "P", "owner_handle": "user-1"}
    ).json()["data"]


def _room(client, project_id: str) -> dict:
    return client.post(
        "/topics",
        json={"project_id": project_id, "title": "大话题", "created_by": "user-1"},
    ).json()["data"]


def _thread(client, room_id: str, title: str = "子活") -> dict:
    return client.post(f"/topics/{room_id}/split", json={"title": title}).json()["data"]


def _record_screens(stub_hooks) -> list[str]:
    """每一次「起一块屏幕」的 topic id。起屏幕就是起容器，这是唯一看得见它的地方。"""
    seen: list[str] = []
    original = stub_hooks.ensure_ready

    async def _spy(**kw):
        seen.append(str(kw.get("topic_id")))
        return await original(**kw)

    stub_hooks.ensure_ready = _spy
    return seen


def _drain_until_done(ws) -> list[dict]:
    frames: list[dict] = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] in ("done", "error"):
            break
    return frames


def _blocks(client, place_id: str) -> list[dict]:
    return client.get(f"/topics/{place_id}/blocks").json()["data"]["data"]


def test_writing_on_a_thread_wakes_the_room_to_relay_it(client, stub_hooks):
    p = _project(client)
    room = _room(client, p["id"])
    thread = _thread(client, room["id"])
    _wait_work_idle()

    screens = _record_screens(stub_hooks)
    before = len(_blocks(client, thread["id"]))
    with client.websocket_connect(chat_ws_url(thread["id"], "user-1")) as ws:
        ws.send_json({"type": "message", "content": "这条先别做了", "summon": True})
        frames = _drain_until_done(ws)
    _wait_work_idle()

    # 说的话落在人说话的地方。
    assert [f for f in frames if f["type"] == "user_block"], frames
    said = [b["content"] for b in _blocks(client, thread["id"])]
    assert len(said) > before
    assert "这条先别做了" in said

    # 被叫醒的是房间，而且这条活一块屏幕都没起。
    assert thread["id"] not in screens, "为一条活起了屏幕——这是在复活容器"
    assert screens == [room["id"]], f"叫醒的不是房间：{screens}"
    prompt = stub_hooks.last_prompt or ""
    assert thread["id"] in prompt, "不说是哪条活，房间不知道该找哪个分身"
    assert "这条先别做了" in prompt, "人说的话没带过去"
    assert "子活" in prompt


def test_a_thread_says_done_rather_than_spinning(client, stub_hooks):
    """界面上不能一直转圈：这一轮不在这条活上跑，就得当场说完了。"""
    p = _project(client)
    room = _room(client, p["id"])
    thread = _thread(client, room["id"])
    _wait_work_idle()

    with client.websocket_connect(chat_ws_url(thread["id"], "user-1")) as ws:
        ws.send_json({"type": "message", "content": "进度怎么样", "summon": True})
        frames = _drain_until_done(ws)
    _wait_work_idle()

    assert frames[-1]["type"] == "done"
    assert not [f for f in frames if f["type"] == "assistant_block"], (
        "这条活自己答了——它没有会话，这答案只能来自一个刚被起出来的容器"
    )


def test_a_room_still_answers_on_its_own_line(client, stub_hooks):
    """改的只是活那条路：房间自己被 @，照旧自己跑这一轮。"""
    p = _project(client)
    room = _room(client, p["id"])

    screens = _record_screens(stub_hooks)
    with client.websocket_connect(chat_ws_url(room["id"], "user-1")) as ws:
        ws.send_json({"type": "message", "content": "在吗", "summon": True})
        _drain_until_done(ws)
    _wait_work_idle()

    assert screens == [room["id"]]
    kinds = [b["author_type"] for b in _blocks(client, room["id"])]
    assert "ai" in kinds, "房间没答话"
