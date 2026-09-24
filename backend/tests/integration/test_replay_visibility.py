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

import pytest

from app.api.deps import get_chat_service
from app.domain.agent.chat import ChatService
from app.main import app
from tests.conftest import StubChannel, settle_turn, stub_compute
from tests.integration.conftest import chat_ws_url, post_project


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
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/replay-ws",
        compute=stub_compute(screen),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    return screen


def _project_and_topic(client) -> str:
    pr = post_project(client, json={"name": "Replay"})
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
    """Send one addressed message and let its turn finish (however it finishes).

    @ 放在句末，和人打字的顺序一样。放句首的话，下面那条状态行引用最早一条消息
    时，截断的那几十个字全被席位 token 占掉，读的人认不出是哪一批。"""
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": f"{text} @芝士"})
        while ws.receive_json()["type"] != "done":
            pass


def _system_lines(client, topic_id: str) -> list[str]:
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    return [b["content"] for b in blocks if b["author_type"] == "platform"]


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


class WorkingScreen(StubChannel):
    """A session that takes the prompt, starts on it, and is still working."""

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        del reply
        self.starts(topic_id)
        self.acknowledges(topic_id, prompt)
        self.uses(topic_id, "Bash", command="sleep 600")


def _restarted_mid_turn(client, first: str) -> tuple[str, StubChannel, ChatService]:
    """A room whose turn was delivered, then the backend was replaced under it.

    Nothing in memory survives — not the platform's record of the turn, not the
    old process's subscription — and the new process listens to the screen that
    outlived it. Returns the room, the new process's screen and its service.
    """
    project_id = post_project(client, json={"name": "Restart"}).json()["data"]["id"]
    topic_id = client.post(
        "/topics",
        json={"project_id": project_id, "title": "换进程", "created_by": "user-1"},
    ).json()["data"]["id"]
    room = uuid.UUID(topic_id)

    before = WorkingScreen()
    app.dependency_overrides[get_chat_service] = lambda: ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/replay-ws",
        compute=stub_compute(before),
    )
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": f"{first} @芝士"})
        while True:
            frame = ws.receive_json()
            if frame["type"] == "event_block" and "sleep 600" in str(frame["block"]):
                break

    client.portal.call(before.runtime._close_topic, room)
    after = StubChannel()
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/replay-ws",
        compute=stub_compute(after),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    client.portal.call(after.runtime.ensure_subscription, uuid.UUID(project_id), room)
    return topic_id, after, service


def test_a_turn_that_outlives_its_backend_does_not_replay_its_batch(client):
    """dev 一合并就重新部署：一轮跑到一半，后端进程被换掉，机器上的会话照常把活
    干完，Stop 落到新进程上。那一轮送进去的消息芝士已经读过、答过了 —— 下一次
    @ 它，prompt 里不该再出现它们，更不该一次次累加成「第 3 次送进轮次」。"""
    topic_id, after, service = _restarted_mid_turn(client, "第一句")
    room = uuid.UUID(topic_id)

    after.returns(room, "Bash", "done", command="sleep 600")
    after.says(room, "第一句已处理")
    after.stops(room, "第一句已处理")
    client.portal.call(settle_turn, service, room)

    _say(client, topic_id, "第二句")
    assert after.last_prompt is not None
    assert "第二句" in after.last_prompt
    assert "第一句" not in after.last_prompt, after.last_prompt


@pytest.mark.parametrize("worked_first", [True, False])
def test_a_batch_whose_session_fails_after_a_restart_is_still_replayed(
    client, worked_first
):
    """换进程之后，会话在新进程上以失败收尾（额度用完、API 拒绝）。那批消息没有被
    处理完，之后会话自己起的一轮干净地停下，也不能把它们标成已读。失败前它可能
    还在新进程上干过活，也可能一句话都没来得及说。"""
    topic_id, after, service = _restarted_mid_turn(client, "第一句")
    room = uuid.UUID(topic_id)

    if worked_first:
        after.uses(room, "Bash", command="make test")
    after.hook(
        room,
        hook_event_name="StopFailure",
        session_id="sess-test-1",
        error="rate_limit",
    )
    client.portal.call(settle_turn, service, room)

    after.uses(room, "Bash", command="ls")
    after.stops(room, "顺手看了一眼")
    client.portal.call(settle_turn, service, room)

    _say(client, topic_id, "第二句")
    assert after.last_prompt is not None
    assert "第二句" in after.last_prompt
    assert "第一句" in after.last_prompt, after.last_prompt


class DiesOnceScreen(SilentScreen):
    """The first session dies on its prompt; the machine is healthy after."""

    def __init__(self, **timeouts: float) -> None:
        super().__init__(**timeouts)
        self.dead = True

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        if not self.dead:
            StubChannel.emit_turn(self, topic_id, prompt, reply)

    async def confirm_alive(self, screen: object) -> bool:
        return not self.dead


def test_a_failed_batch_is_not_swallowed_by_a_later_clean_stop(client):
    """送达不等于读过：那一轮是死掉的，它的消息必须留给下一轮重发。之后会话自己
    起的一轮干干净净地停下，也不能顺手把这批消息标成已读 —— 否则就是丢消息。"""
    project_id = post_project(client, json={"name": "Dies"}).json()["data"]["id"]
    topic_id = client.post(
        "/topics",
        json={"project_id": project_id, "title": "死过一次", "created_by": "user-1"},
    ).json()["data"]["id"]
    room = uuid.UUID(topic_id)
    screen = DiesOnceScreen(
        idle_suspect_s=0.2, hard_ceiling_s=10.0, delivery_timeout_s=0.2
    )
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/replay-ws",
        compute=stub_compute(screen),
    )
    app.dependency_overrides[get_chat_service] = lambda: service

    _say(client, topic_id, "死掉那句")
    screen.dead = False

    # A turn the session starts by itself, ending cleanly.
    client.portal.call(screen.runtime.ensure_subscription, uuid.UUID(project_id), room)
    screen.uses(room, "Bash", command="ls")
    screen.stops(room, "顺手看了一眼")
    client.portal.call(settle_turn, service, room)

    _say(client, topic_id, "下一句")
    assert screen.last_prompt is not None
    assert "下一句" in screen.last_prompt
    assert "死掉那句" in screen.last_prompt, screen.last_prompt
