"""叫芝士来读没人 @ 过的那条消息。

没 @ 的消息从来不会丢：它在待读窗口里等着下一轮把它捎上。但「等下一轮」在一个
安静的房间里等于永远，而房间安静恰恰是忘了 @ 之后的常态——有人贴完需求等了八
分钟，追问「你有看到我的问题嘛」，全程没人接。这个接口把那次等待换成一次点击。
"""

import time

from tests.integration.conftest import (
    chat_ws_url,
    post_project,
    room_agent_seat,
    session_auth_headers,
)
from tests.support.hang import HANG_S


def _project_and_topic(client, owner: str = "user-1") -> str:
    p = post_project(client, json={"name": "P", "owner_handle": owner}).json()["data"]
    t = client.post(
        "/topics", json={"project_id": p["id"], "title": "T", "created_by": owner}
    ).json()["data"]
    return t["id"]


def _say_without_summoning(client, topic_id: str, text: str) -> None:
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": text})
        while ws.receive_json()["type"] != "done":
            pass


def _wait_for_prompt(stub_hooks, needle: str) -> str:
    deadline = time.monotonic() + HANG_S
    while time.monotonic() < deadline:
        if needle in (stub_hooks.last_prompt or ""):
            return stub_hooks.last_prompt or ""
        time.sleep(0.01)
    raise AssertionError(f"芝士始终没拿到这句话：{stub_hooks.last_prompt!r}")


def test_summon_hands_over_what_nobody_addressed(client, stub_hooks):
    topic_id = _project_and_topic(client)
    _say_without_summoning(client, topic_id, "这个分页方案你看下")
    assert "这个分页方案你看下" not in (stub_hooks.last_prompt or "")

    r = client.post(f"/topics/{topic_id}/summon", json={"author": "user-1"})
    assert r.status_code == 200
    assert r.json()["data"]["started"] is True

    # 它拿到的东西，和「当时就 @ 了它」一模一样。
    _wait_for_prompt(stub_hooks, "这个分页方案你看下")


def test_summon_does_not_repost_the_message(client, stub_hooks):
    topic_id = _project_and_topic(client)
    _say_without_summoning(client, topic_id, "这个分页方案你看下")

    client.post(f"/topics/{topic_id}/summon", json={"author": "user-1"})
    _wait_for_prompt(stub_hooks, "这个分页方案你看下")

    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    said = [
        b["content"]
        for b in blocks
        if b["author"] == "user-1" and b["kind"] == "message"
    ]
    # 补一条一模一样的消息，读的人就得自己分辨哪条是真的。
    assert said == ["这个分页方案你看下"]


def _wait_until_read(client, topic_id: str) -> None:
    """等到那条消息真的被某一轮读进去了（它自己身上记着是哪一轮）。"""
    deadline = time.monotonic() + HANG_S
    while time.monotonic() < deadline:
        blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
        if any(
            b["author"] == "user-1"
            and b["kind"] == "message"
            and (b.get("meta") or {}).get("consumed_turn")
            for b in blocks
        ):
            return
        time.sleep(0.02)
    raise AssertionError("那条消息始终没有被任何一轮读进去")


def test_summon_while_it_is_already_working_starts_nothing(client, stub_hooks):
    topic_id = _project_and_topic(client)
    _say_without_summoning(client, topic_id, "这个分页方案你看下")
    client.post(f"/topics/{topic_id}/summon", json={"author": "user-1"})
    _wait_for_prompt(stub_hooks, "这个分页方案你看下")

    # 正在跑的那一轮会自己把后来的消息接过去，再开一轮只是排在它后面白烧算力。
    r = client.post(f"/topics/{topic_id}/summon", json={"author": "user-1"})
    assert r.json()["data"] == {"started": False, "reason": "working"}


def test_summon_after_someone_else_already_asked_starts_nothing(client, stub_hooks):
    topic_id = _project_and_topic(client)
    _say_without_summoning(client, topic_id, "这个分页方案你看下")
    client.post(f"/topics/{topic_id}/summon", json={"author": "user-1"})
    _wait_for_prompt(stub_hooks, "这个分页方案你看下")
    _wait_until_read(client, topic_id)
    before = client.get(f"/topics/{topic_id}/blocks").json()["data"]["total"]

    # 那条消息已经被读进去了。两个人先后按这一下，第二下不该再花一次钱。
    r = client.post(f"/topics/{topic_id}/summon", json={"author": "user-1"})
    assert r.json()["data"] == {"started": False, "reason": "nothing_pending"}
    time.sleep(0.3)
    assert client.get(f"/topics/{topic_id}/blocks").json()["data"]["total"] == before


def _a_room_with_two_teammates(client) -> tuple[str, str]:
    """A room with the project's default 芝士 and one more AI teammate in it.

    Returns the room and the second teammate's seat. The default seat is read
    BEFORE the second one joins: `room_agent_seat` answers only for a room that
    hosts exactly one agent, which is the point of asking it here.
    """
    p = post_project(client, json={"name": "P", "owner_handle": "alice"}).json()["data"]
    topic_id = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T", "created_by": "alice"},
    ).json()["data"]["id"]
    default = room_agent_seat(client, topic_id)
    made = client.post(f"/projects/{p['id']}/agents", json={"handle": "opus"})
    assert made.status_code == 200, made.text
    teammate = made.json()["data"]["seat_handle"]
    joined = client.post(
        f"/topics/{topic_id}/members",
        json={"handle": teammate, "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert joined.status_code == 200, joined.text
    assert teammate != default
    return topic_id, teammate


def _say(client, topic_id: str, text: str, author: str = "alice") -> None:
    """Say one thing in the room and wait for whatever it started to be over."""
    with client.websocket_connect(chat_ws_url(topic_id, author)) as ws:
        ws.send_json({"type": "message", "content": text})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass


def _its_turns_die(channel, monkeypatch) -> None:
    """让这个房间里的轮次死在收尾之前：起得来、读得到话，然后没有下文。

    这正是「重试」按钮出现的那个状态（`test_replay_visibility` 里那份
    `SilentScreen` 同源）。轮次没干净收尾，它读进去的那批消息就不会被盖上读过的
    戳，于是还留在待读窗口里 —— 待读窗口里有消息，才是那一下点击的前提。
    """
    channel.alive = False
    monkeypatch.setattr(channel, "emit_turn", lambda *a, **k: None)


def _handed_over(client, topic_id: str) -> list[str]:
    return [
        b["content"]
        for b in client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
        if "把之前的消息交给了" in b["content"] or "交出了之前的消息" in b["content"]
    ]


def _wait_for_handover(client, topic_id: str) -> list[str]:
    """那一行是那一轮开跑时写下来的，所以要点几下才看得到。"""
    deadline = time.monotonic() + HANG_S
    while time.monotonic() < deadline:
        lines = _handed_over(client, topic_id)
        if lines:
            return lines
        time.sleep(0.02)
    return []


def test_summon_hands_the_room_to_the_teammate_the_messages_named(
    client, stub_hooks, monkeypatch
):
    """房间里坐着两位 AI 队友时，交给的是**消息点名的那位**。

    失败提示上的「重试」按钮走的就是这个接口，而它以前取的是房间的默认席位：
    另一位队友的轮次失败之后一点重试，就换成默认芝士来接 —— 而默认芝士那一轮的
    待读窗口里根本没有点名给那位队友的消息（`_addressed_to` 按收件人过滤），于是
    它接了一轮却读不到真正找它的那句话。
    """
    topic_id, teammate = _a_room_with_two_teammates(client)
    _its_turns_die(stub_hooks, monkeypatch)
    _say(client, topic_id, f"<@{teammate}> 这个分页方案你看下")
    # 那一轮死在收尾之前，所以这条消息还等着有人读；下面那一下就是「重试」。

    r = client.post(
        f"/topics/{topic_id}/summon",
        json={"author": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["started"] is True, r.text

    assert _wait_for_handover(client, topic_id) == [
        f"<@alice> 把之前的消息交给了 <@{teammate}>"
    ]
