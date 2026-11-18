"""一轮读入的消息有上限；超出的更早那部分没有交给芝士，它的已读位置却照样越过
它们。所以这一轮必须告诉它：还有几条、是谁说的、从哪里往前读——否则那几位等的回答
就悄悄没了。"""

import re
import time

from app.core.config import settings
from tests.conftest import wait_work_idle
from tests.integration.conftest import (
    join_project_team,
    post_message,
    post_project,
    room_socket,
    session_auth_headers,
)

_TURN_NOTICE = re.compile(
    r'<platform_notice type="turn">\n(.*?)\n</platform_notice>', re.S
)


def _channel(client) -> str:
    """A channel of two people: what one of them says without naming the
    teammate does not wake it, and waits for its next turn."""
    project = post_project(client, json={"name": "P"}, owner="user-1").json()["data"]
    join_project_team(client, project["id"], "user-2")
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "T"},
        headers=session_auth_headers("user-1"),
    ).json()["data"]
    joined = client.post(
        f"/topics/{topic['id']}/join", headers=session_auth_headers("user-2")
    )
    assert joined.status_code == 200, joined.text
    return topic["id"]


def _say(client, topic_id: str, text: str, *, by: str = "user-1") -> None:
    with room_socket(client, topic_id, by) as ws:
        post_message(client, topic_id, by, {"content": text})
        while ws.receive_json()["type"] != "done":
            pass


def _wait_for_prompt(stub_hooks, needle: str) -> str:
    for _ in range(500):
        if needle in (stub_hooks.last_prompt or ""):
            return stub_hooks.last_prompt or ""
        time.sleep(0.01)
    raise AssertionError(f"芝士始终没拿到这句话：{stub_hooks.last_prompt!r}")


def _message_id(client, topic_id: str, content: str) -> str:
    blocks = client.get(
        f"/topics/{topic_id}/blocks", headers=session_auth_headers("user-1")
    ).json()["data"]["data"]
    return next(b["id"] for b in blocks if b["content"] == content)


def _left_out_notice(prompt: str, before: str) -> str | None:
    """The turn notice that points at ``before`` as where to read back from."""
    return next((n for n in _TURN_NOTICE.findall(prompt) if before in n), None)


def test_messages_past_the_window_are_counted_named_and_pointed_at(
    client, stub_hooks, monkeypatch
):
    monkeypatch.setattr(settings, "agent_input_window_messages", 3)
    topic_id = _channel(client)
    _say(client, topic_id, "@芝士 开工")
    _wait_for_prompt(stub_hooks, "开工")
    wait_work_idle()

    for text in ("旧话一", "旧话二", "旧话三", "旧话四"):
        _say(client, topic_id, text, by="user-2")
    _say(client, topic_id, "@芝士 看看上面")
    prompt = _wait_for_prompt(stub_hooks, "看看上面")

    # The window takes the newest three: 旧话三, 旧话四 and the summons.
    assert "旧话一" not in prompt and "旧话二" not in prompt
    oldest_handed = _message_id(client, topic_id, "旧话三")
    notice = _left_out_notice(prompt, oldest_handed)
    assert notice is not None, "没交给它的那两条，这一轮得说出在哪里往前读"
    assert "2" in notice, "说清有几条没交给它"
    assert "user-2" in notice, "说清是谁的话没交给它"

    # Read back from where it points, the two are exactly what is there.
    r = client.get(
        f"/topics/{topic_id}/history",
        params={"before": oldest_handed, "limit": 2},
        headers=session_auth_headers("user-1"),
    )
    assert r.status_code == 200, r.text
    said = [m.get("content") for m in r.json()["data"]["data"]]
    assert "旧话一" in said and "旧话二" in said


def test_a_window_that_holds_everything_says_nothing_more(client, stub_hooks):
    topic_id = _channel(client)
    _say(client, topic_id, "@芝士 开工")
    _wait_for_prompt(stub_hooks, "开工")
    wait_work_idle()

    _say(client, topic_id, "补一句", by="user-2")
    _say(client, topic_id, "@芝士 继续")
    prompt = _wait_for_prompt(stub_hooks, "继续")

    handed = _message_id(client, topic_id, "补一句")
    assert _left_out_notice(prompt, handed) is None
