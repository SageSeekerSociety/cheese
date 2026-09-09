"""叫芝士来读没人 @ 过的那条消息。

没 @ 的消息从来不会丢：它在待读窗口里等着下一轮把它捎上。但「等下一轮」在一个
安静的房间里等于永远，而房间安静恰恰是忘了 @ 之后的常态——有人贴完需求等了八
分钟，追问「你有看到我的问题嘛」，全程没人接。这个接口把那次等待换成一次点击。
"""

import time

from tests.integration.conftest import chat_ws_url


def _project_and_topic(client, owner: str = "user-1") -> str:
    p = client.post("/projects", json={"name": "P", "owner_handle": owner}).json()[
        "data"
    ]
    t = client.post(
        "/topics", json={"project_id": p["id"], "title": "T", "created_by": owner}
    ).json()["data"]
    return t["id"]


def _say_without_summoning(client, topic_id: str, text: str) -> None:
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": text, "summon": False})
        while ws.receive_json()["type"] != "done":
            pass


def _wait_for_prompt(stub_hooks, needle: str) -> str:
    for _ in range(500):
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
        if b["author_type"] == "human" and b["kind"] == "message"
    ]
    # 补一条一模一样的消息，读的人就得自己分辨哪条是真的。
    assert said == ["这个分页方案你看下"]


def _wait_until_read(client, topic_id: str) -> None:
    """等到那条消息真的被某一轮读进去了（它自己身上记着是哪一轮）。"""
    for _ in range(500):
        blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
        if any(
            b["author_type"] == "human"
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
