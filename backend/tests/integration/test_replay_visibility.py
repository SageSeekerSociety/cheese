"""重放可见 (#416): a batch of messages that keeps being re-sent must say so.

A turn only stamps `consumed_turn` when it FINISHES, which is deliberate — a
turn that dies must not eat the message. The cost is that a failing turn
re-sends the identical batch next turn, and the next, with nothing anywhere
saying it is the same batch. From the room that is indistinguishable from "this
topic is broken", and the difference is the whole diagnosis.

These tests drive real turns through the WS against a provider whose result is
an error, and assert what a person sitting in the room would see.
"""

import uuid

from app.api.deps import get_chat_service
from app.domain.agent.chat import ChatService
from app.main import app
from tests.conftest import StubChannel, stub_compute
from tests.integration.conftest import chat_ws_url


class SilentScreen(StubChannel):
    """A session that takes the prompt and then says nothing at all.

    The turn ends the way a dead session's turn ends — the watchdog gives up and
    closes it as an error — which is the shape that leaves the pending batch
    unconsumed (nothing ever reaches `mark_consumed`). The timeouts are squeezed
    so the test does not sit through the production ones.
    """

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        del topic_id, prompt, reply

    async def confirm_alive(self, screen: object) -> bool:
        # Silence alone is not failure; this fixture models a confirmed dead
        # process so each replay attempt reaches a terminal result.
        return False


def _use_failing_agent(client, monkeypatch) -> SilentScreen:
    """A topic whose every turn dies, with nothing else re-prompting it.

    A turn that dies to the substrate is not re-run by the platform — it fails
    loud once and waits for a person — so nothing schedules a second send of the
    batch behind these tests' backs. That is what makes it safe to count how
    many times ONE batch is sent, which is the whole assertion here.
    """
    # The first silence check asks the channel whether the process is alive.
    # Keep that check short; the fake then confirms death. The wall-clock
    # ceiling only records elapsed time and does not terminate the turn.
    screen = SilentScreen(
        idle_suspect_s=0.2, hard_ceiling_s=10.0, delivery_timeout_s=0.2
    )

    service = ChatService(
        session_factory=client.test_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/replay-ws",
        compute=stub_compute(screen),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    return screen


def _project_and_topic(client) -> str:
    pr = client.post("/projects", json={"name": "Replay"})
    tr = client.post(
        "/topics",
        json={
            "project_id": pr.json()["data"]["id"],
            "title": "重放",
            "created_by": "user-1",
        },
    )
    return tr.json()["data"]["id"]


def _say(client, topic_id: str, text: str) -> None:
    """Send one message and let its turn finish (however it finishes)."""
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": text, "summon": True})
        while ws.receive_json()["type"] != "done":
            pass


def _system_lines(client, topic_id: str) -> list[str]:
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    return [b["content"] for b in blocks if b["author_type"] == "system"]


def _replay_notices(client, topic_id: str) -> list[str]:
    """房间里「又重投了一次」那几行——按类别码找，不按开头那个字符找。"""
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    return [
        b["content"]
        for b in blocks
        if (b.get("meta") or {}).get("event_type") == "prompt_replayed"
    ]


def test_a_repeatedly_replayed_batch_is_announced_in_the_room(client, monkeypatch):
    _use_failing_agent(client, monkeypatch)
    topic_id = _project_and_topic(client)

    # Three failing turns. The first message rides all three prompts, so by the
    # third one the batch is on its third attempt.
    _say(client, topic_id, "第一句")
    assert not _replay_notices(client, topic_id)
    _say(client, topic_id, "第二句")
    assert not _replay_notices(client, topic_id), "两次还不算模式，不该已经喊出来"

    _say(client, topic_id, "第三句")
    notices = _replay_notices(client, topic_id)
    assert len(notices) == 1, notices
    notice = notices[0]
    # It has to name the count...
    assert "第 3 次" in notice
    # ...how many are stuck...
    assert "这 3 条消息" in notice
    # ...and enough to identify the batch: the OLDEST message, the one stuck
    # longest. Not all of them — a status line must not become a second copy of
    # the conversation.
    assert "第一句" in notice
    assert "第三句" not in notice
    assert notice.count("\n") == 0, "现场提示必须是一行"


def test_a_turn_that_finishes_never_announces_a_replay(client):
    """The counter must not fire on ordinary conversation: a turn that completes
    consumes its batch, so nothing is ever on a third attempt."""
    topic_id = _project_and_topic(client)
    for text in ("一", "二", "三", "四"):
        _say(client, topic_id, text)

    assert not _replay_notices(client, topic_id)


def test_the_notice_throttles_instead_of_burying_the_conversation(client, monkeypatch):
    """A topic retrying for an hour must not fill the room with its own status:
    after the first warning the state is known, so the reminder goes quiet."""
    _use_failing_agent(client, monkeypatch)
    topic_id = _project_and_topic(client)

    for i in range(8):
        _say(client, topic_id, f"消息{i}")

    notices = _replay_notices(client, topic_id)
    # 8 failing turns, but only the third one speaks (the next is the 10th).
    assert len(notices) == 1, notices
    assert "第 3 次" in notices[0]
