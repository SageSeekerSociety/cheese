"""一条事件的作者是参与者，还是平台 —— 四条判据。

人和 agent 是同一种参与者（结论 1），所以事件行上再没有一个字段回答「这句是人说
的还是 AI 说的」：差别写在署名上。四条判据合起来是「档位合并之后，轮次输入的账
目仍然是对的」：

1. 同一条消息由人发和由芝士发，档位一样，署名不一样；
2. 芝士对着房间说的一句话是一条待读输入，被下一轮读进去，而且只读一次；
3. 芝士**这一轮自己产出的**那条回复不是待读输入 —— 否则下一轮会把它当成新进来的
   一句话，它对着自己的上一句再答一遍，而那句话本来就在它的 transcript 里。
4. 一次发送里的「文字 + 图片」两块都是待读输入 —— 附件块的轮次 id 跟着正文块走，
   所以「有没有轮次号」这一件事分不出产出和到达，它得连着署名一起看。

②③④ 都只看交到芝士手上的 prompt：把哪一条算成输入，读得出来的就是「下一轮看见了
什么」。
"""

import time
import uuid

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import chat_ws_url, session_auth_headers

# A valid 1x1 transparent PNG — small but real image bytes.
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _room(client, owner: str = "user-1") -> tuple[str, str, dict]:
    """一个房间，外加这个房间里芝士的凭据。"""
    project = client.post(
        "/projects", json={"name": "P", "owner_handle": owner}
    ).json()["data"]
    topic = client.post(
        "/topics", json={"project_id": project["id"], "title": "T", "created_by": owner}
    ).json()["data"]
    token = mint_scoped_token(project_id=project["id"], topic_id=topic["id"])
    return project["id"], topic["id"], {"X-Cheese-Token": token}


def _say(client, topic_id: str, text: str, *, summon: bool) -> None:
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": text, "summon": summon})
        while ws.receive_json()["type"] != "done":
            pass


def _cheese_says(client, topic_id: str, headers: dict, text: str) -> dict:
    r = client.post(
        f"/topics/{topic_id}/messages",
        json={"content": text, "request_id": str(uuid.uuid4())},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _blocks(client, topic_id: str) -> list[dict]:
    return client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]


def _wait_for_prompt(stub_hooks, needle: str) -> str:
    for _ in range(500):
        if needle in (stub_hooks.last_prompt or ""):
            return stub_hooks.last_prompt or ""
        time.sleep(0.01)
    raise AssertionError(f"芝士始终没拿到这句话：{stub_hooks.last_prompt!r}")


def test_a_person_and_an_agent_sign_the_same_kind_of_message(client, stub_hooks):
    """①同一条消息由人发和由芝士发：档位一样，署名不一样。"""
    _project, topic_id, headers = _room(client)
    _say(client, topic_id, "人说的一句", summon=False)
    published = _cheese_says(client, topic_id, headers, "芝士说的一句")

    said = {
        b["content"]: b for b in _blocks(client, topic_id) if b["kind"] == "message"
    }
    person, cheese = said["人说的一句"], said["芝士说的一句"]

    assert person["author_type"] == cheese["author_type"], (
        "人和 agent 是同一种参与者，事件行上不该分出两档"
    )
    assert person["author"] == "user-1"
    assert cheese["author"] == published["author"] != "user-1", "两句话的区别写在署名上"
    assert stub_hooks.last_prompt is None, "这两条都没叫它，一轮也不该开"


def test_what_an_agent_says_in_the_room_is_read_once(client, stub_hooks):
    """②芝士对着房间说的一句话是待读输入，下一轮读进去，而且只读一次。"""
    _project, topic_id, headers = _room(client)
    # 不在任何一轮里说的：这一句和人说的一句一样，在待读窗口里等下一轮。
    _cheese_says(client, topic_id, headers, "接口我已经改完了")

    r = client.post(f"/topics/{topic_id}/summon", json={"author": "user-1"})
    assert r.json()["data"]["started"] is True, "队友说的话同样是没人读过的输入"
    first = _wait_for_prompt(stub_hooks, "接口我已经改完了")
    assert first.count("接口我已经改完了") == 1

    _say(client, topic_id, "那就继续", summon=True)
    second = _wait_for_prompt(stub_hooks, "那就继续")
    assert "接口我已经改完了" not in second, "读过一次就不再重投"


def test_an_agents_own_turn_output_is_not_an_input(client, stub_hooks):
    """③芝士这一轮自己产出的回复不是待读输入，下一轮不会拿它当新话读。"""
    _project, topic_id, _headers = _room(client)
    _say(client, topic_id, "开工", summon=True)
    _wait_for_prompt(stub_hooks, "开工")

    _say(client, topic_id, "继续", summon=True)
    second = _wait_for_prompt(stub_hooks, "继续")

    assert stub_hooks.reply not in second, (
        "上一轮自己说出口的话不是下一轮的输入 —— 它已经在 transcript 里"
    )
    assert "开工" not in second, "答过的那句也不重投"


def test_a_text_and_an_image_sent_together_both_reach_the_next_turn(client, stub_hooks):
    """④一次发送「文字 + 图片」：下一轮两块都在，而且各只有一份。

    图片块不是「没有轮次号」的那一类 —— 一次发送是一件事，附件块落库时带的就是正
    文块的 id。只看轮次号的话这张图一个待读标记都不带，于是只剩位置水位兜底，而
    那条兜底会被之后任何一条拿到回执的消息推过去，图就再也读不到了。
    """
    _project, topic_id, _headers = _room(client)
    client.headers.update(session_auth_headers("user-1"))
    upload = client.post(
        f"/topics/{topic_id}/attachments",
        files={"file": ("screenshot.png", PNG_1PX, "image/png")},
    )
    assert upload.status_code == 200, upload.text
    att = upload.json()["data"]

    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json(
            {
                "type": "message",
                "content": "看看这张截图",
                "summon": False,
                "attachments": [att],
            }
        )
        while ws.receive_json()["type"] != "done":
            pass

    kinds = [b["kind"] for b in _blocks(client, topic_id)]
    assert kinds == ["message", "attachment"], "一次发送写下两块"

    r = client.post(f"/topics/{topic_id}/summon", json={"author": "user-1"})
    assert r.json()["data"]["started"] is True
    prompt = _wait_for_prompt(stub_hooks, "看看这张截图")
    assert prompt.count("看看这张截图") == 1
    assert prompt.count("[user-1] 发来一张图片") == 1, (
        "图片和它旁边的那句话是同一次发送，两块一起进这一轮"
    )
    assert att["path"] in prompt
