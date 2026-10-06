"""重放可见 (#416): a batch confirmed not sent must say when it is retried —
in the 现场, where whoever wonders why the room is slow looks.

A turn only stamps `consumed_turn` when it FINISHES. A failed launch has sent
nothing and its batch can be retried. Once a native input is registered, its
outcome must be reconciled before that batch can be sent again; an error result
and another work's clean Stop do not consume or release it.

These tests drive real turns through the WS against a provider whose result is
an error, and assert what a person sitting in the room would see.
"""

import uuid

import pytest
from sqlalchemy import select

from app.api.deps import get_chat_service
from app.domain.agent.chat import ChatService
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.block.models import Block, consumed_turn
from app.domain.delivery.models import NativeInput
from app.main import app
from tests.conftest import StubChannel, settle_turn, stub_compute, wait_work_idle
from tests.integration.conftest import (
    chat_ws_url,
    in_thread,
    post_message,
    post_project,
    session_auth_headers,
)


class SilentScreen(StubChannel):
    """A session whose process is gone: it takes the prompt and says nothing.

    Its runner answers that the process is not alive, and the turn ends the way
    a dead session's turn ends — as an error — which is the shape that leaves
    the pending batch unconsumed (nothing ever reaches `mark_consumed`).
    """

    def __init__(self, **policy: float) -> None:
        super().__init__(**policy)
        self.alive = False

    def emit_turn(
        self,
        topic_id: uuid.UUID,
        prompt: str,
        reply: str,
        *,
        agent: str | None = None,
    ) -> None:
        del topic_id, prompt, reply


class UnlaunchedScreen(StubChannel):
    """Fail before a runner or native input exists, so retry is unambiguous."""

    async def precheck(self, session, *, needs_place):
        raise ScreenSetupError("The executor could not be launched")


def _use_failing_agent(client, monkeypatch) -> UnlaunchedScreen:
    """A topic whose every turn dies, with nothing else re-prompting it.

    A turn that dies to the substrate is not re-run by the platform — it fails
    loud once and waits for a person — so nothing schedules a second send of the
    batch behind these tests' backs. That is what makes it safe to count how
    many times ONE batch is sent, which is the whole assertion here.
    """
    screen = UnlaunchedScreen()

    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/replay-ws",
        compute=stub_compute(screen),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    return screen


def _project_and_topic(client) -> str:
    pr = post_project(client, json={"name": "Replay"}, owner="user-1")
    tr = client.post(
        "/topics",
        json={
            "project_id": pr.json()["data"]["id"],
            "title": "重放",
        },
    )
    # 芝士 answers in a 支线 of the channel: that is where its turns run.
    return in_thread(client, tr.json()["data"]["id"], "user-1")


def _say(client, topic_id: str, text: str) -> None:
    """Send one addressed message and let its turn finish (however it finishes).

    @ 放在句末，和人打字的顺序一样。放句首的话，下面那条状态行引用最早一条消息
    时，截断的那几十个字全被席位 token 占掉，读的人认不出是哪一批。

    等的是**自己这条消息**的那一轮，不是「随便哪一轮的收尾」。房间的频道带重放
    缓冲（`InProcessBroker.subscribe(replay=True)`），而一条 socket 从订上到自己的
    消息被提交进去之间有一个窗口：上一轮收尾的 `done` 正好落在这个窗口里，就在
    「自己的消息还没进房间」的时候先到了手上。这一条 `done` 是不是自己那一轮的，
    帧上没有任何标识（`{"type": "done"}`），只能靠先后认——所以先等自己那条消息
    真的落进房间（它一定由自己的 `user_block` 带回，而且一定排在自己这一轮的帧
    前面），再等收尾。等不到就继续等，不是把断言放宽。

    这是 2026-09-27 CI 上两条随机红的成因之一（`-n auto` 并发下更常撞上）：先收到
    上一轮的 `done` 就返回，紧接着读到的 `after.last_prompt` 还是上一轮的样子。"""
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        post_message(client, topic_id, "user-1", {"content": f"{text} @芝士"})
        landed = False
        while True:
            frame = ws.receive_json()
            if frame["type"] == "user_block":
                landed = landed or text in str(
                    (frame.get("block") or {}).get("content")
                )
            elif landed and frame["type"] == "done":
                break


def _system_lines(client, topic_id: str) -> list[str]:
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    return [b["content"] for b in blocks if b["author_type"] == "platform"]


def _replay_notices(client, topic_id: str) -> list[str]:
    """现场里「又重投了一次」那几行——按类别码找，不按开头那个字符找。它只在现场
    里，不在对话里。"""

    def replayed(rows: list[dict]) -> list[str]:
        return [
            b["content"]
            for b in rows
            if (b.get("meta") or {}).get("event_type") == "prompt_replayed"
        ]

    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    assert replayed(blocks) == []
    site = client.get(f"/topics/{topic_id}/transcript").json()["data"]["data"]
    return replayed(site)


def _opened_by(client, text: str) -> str:
    """A channel where ``text`` calls 芝士 in the main line; returns the 支线
    it is answered in, once that first turn has finished."""
    pr = post_project(client, json={"name": "Replay"}, owner="user-1")
    room = client.post(
        "/topics",
        json={"project_id": pr.json()["data"]["id"], "title": "重放"},
    ).json()["data"]["id"]
    asked = post_message(client, room, "user-1", {"content": f"{text} @芝士"})
    thread = client.post(
        f"/blocks/{asked['id']}/thread", headers=session_auth_headers("user-1")
    ).json()["data"]["id"]
    wait_work_idle()
    return thread


def test_a_repeatedly_replayed_batch_is_announced_in_the_room(client, monkeypatch):
    _use_failing_agent(client, monkeypatch)

    # Three failing turns. The first message rides all three prompts, so by the
    # third one the batch is on its third attempt.
    topic_id = _opened_by(client, "第一句")
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

    def emit_turn(
        self,
        topic_id: uuid.UUID,
        prompt: str,
        reply: str,
        *,
        agent: str | None = None,
    ) -> None:
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
    project_id = post_project(client, json={"name": "Restart"}, owner="user-1").json()[
        "data"
    ]["id"]
    topic_id = client.post(
        "/topics",
        json={"project_id": project_id, "title": "换进程"},
    ).json()["data"]["id"]
    topic_id = in_thread(client, topic_id, "user-1")

    before = WorkingScreen()
    app.dependency_overrides[get_chat_service] = lambda: ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/replay-ws",
        compute=stub_compute(before),
    )
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        post_message(client, topic_id, "user-1", {"content": f"{first} @芝士"})
        while True:
            frame = ws.receive_json()
            if frame["type"] == "event_block" and "sleep 600" in str(frame["block"]):
                break

    # The old process stops reading, as a replaced backend does. `_detach` takes
    # a seat, not a room, so `_detach(room)` removed nothing and the old reader
    # went on handling the session's records next to the new one.
    client.portal.call(before.runtime.stop_listening)
    # The machine kept its runner; the new process has only the channel to it.
    after = StubChannel()
    after.root = before.root
    after.sessions = before.sessions
    after.calls = before.calls
    for session in after.sessions.values():
        session.channel = after
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/replay-ws",
        compute=stub_compute(after),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    assert client.portal.call(service.recover_sessions) == 1
    return topic_id, after, service


def test_a_turn_that_outlives_its_backend_does_not_replay_its_batch(client):
    """dev 一合并就重新部署：一轮跑到一半，后端进程被换掉，机器上的会话照常把活
    干完，Stop 落到新进程上。那一轮送进去的消息芝士已经读过、答过了 —— 下一次
    @ 它，prompt 里不该再出现它们，更不该一次次累加成「第 3 次送进轮次」。"""
    topic_id, after, service = _restarted_mid_turn(client, "第一句")
    room = uuid.UUID(topic_id)

    after.returns(room, "Bash", "done")
    after.says(room, "第一句已处理")
    after.stops(room, "第一句已处理")
    client.portal.call(settle_turn, service, room)

    _say(client, topic_id, "第二句")
    assert after.last_prompt is not None
    assert "第二句" in after.last_prompt
    assert "第一句" not in after.last_prompt, after.last_prompt


@pytest.mark.parametrize("worked_first", [True, False])
def test_a_batch_whose_session_fails_after_a_restart_stays_held_without_replay(
    client, worked_first
):
    """失败结果不等于处理完成；另一轮的干净 Stop 也不能替原输入完成结算。

    原消息保留原登记身份、未消费且仍被持有，新消息不能把它换一个输入 id 再送。
    """
    topic_id, after, service = _restarted_mid_turn(client, "第一句")
    room = uuid.UUID(topic_id)

    if worked_first:
        after.uses(room, "Bash", command="make test")
    after.says(room, "API Error: Rate limit reached", error="rate_limit")
    after.record(
        room,
        type="result",
        subtype="success",
        is_error=True,
        result="API Error: Rate limit reached",
        terminal_reason="api_error",
    )
    client.portal.call(settle_turn, service, room)

    after.uses(room, "Bash", command="ls")
    after.stops(room, "顺手看了一眼")
    client.portal.call(settle_turn, service, room)

    async def original_input_is_still_held():
        async with client.test_request_factory() as session:
            blocks = list(
                await session.scalars(
                    select(Block).where(Block.conversation_id == room)
                )
            )
            original = next(
                block
                for block in blocks
                if block.author == "user-1" and "第一句" in block.content
            )
            rows = list(
                await session.scalars(
                    select(NativeInput).where(NativeInput.conversation_id == room)
                )
            )
            holders = [row for row in rows if str(original.id) in row.held_block_ids]
            assert len(holders) == 1
            assert holders[0].completed_at is None
            assert str(original.id) not in holders[0].released_block_ids
            assert consumed_turn(original) is None

    client.portal.call(original_input_is_still_held)
    _say(client, topic_id, "第二句")
    assert after.last_prompt is not None
    assert "第二句" in after.last_prompt
    assert "第一句" not in after.last_prompt, after.last_prompt
    client.portal.call(original_input_is_still_held)


class DiesOnceScreen(SilentScreen):
    """The first session dies on its prompt; the machine is healthy after."""

    def emit_turn(
        self,
        topic_id: uuid.UUID,
        prompt: str,
        reply: str,
        *,
        agent: str | None = None,
    ) -> None:
        if self.alive:
            StubChannel.emit_turn(self, topic_id, prompt, reply)


def test_a_failed_batch_is_not_swallowed_by_a_later_clean_stop(client):
    """送达不等于读过：那一轮是死掉的，它的消息必须留给下一轮重发。之后会话自己
    起的一轮干干净净地停下，也不能顺手把这批消息标成已读 —— 否则就是丢消息。"""
    project_id = post_project(client, json={"name": "Dies"}, owner="user-1").json()[
        "data"
    ]["id"]
    topic_id = client.post(
        "/topics",
        json={"project_id": project_id, "title": "死过一次"},
    ).json()["data"]["id"]
    topic_id = in_thread(client, topic_id, "user-1")
    room = uuid.UUID(topic_id)
    screen = DiesOnceScreen()
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/replay-ws",
        compute=stub_compute(screen),
    )
    app.dependency_overrides[get_chat_service] = lambda: service

    _say(client, topic_id, "死掉那句")
    # The runner was only out of reach: it is back, and the backend finds it
    # again.
    screen.alive = True
    assert client.portal.call(service.recover_sessions) == 1

    # A turn the session starts by itself, ending cleanly.
    screen.uses(room, "Bash", command="ls")
    screen.stops(room, "顺手看了一眼")
    client.portal.call(settle_turn, service, room)

    _say(client, topic_id, "下一句")
    assert screen.last_prompt is not None
    assert "下一句" in screen.last_prompt
    assert "死掉那句" in screen.last_prompt, screen.last_prompt
