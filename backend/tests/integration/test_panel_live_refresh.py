"""面板必须跟着变 (§3.1.1): a change to a platform resource tells the room its
panel is stale WHILE the turn runs, not only in the record it leaves behind.

The frame is sent by the API handler that made the change. That handler is the
one place that knows the change happened: an agent may reach it through a
platform tool, `platform_request`, or any other client, and a call can fail.
That a tool call was made is not evidence of either, so a call the backend
never received refreshes nothing.
"""

import asyncio
import time
import uuid

import pytest
from sqlalchemy import select

from app.api.deps import get_chat_service
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.runtime import get_broker
from app.domain.block.models import Block
from app.main import app
from tests.conftest import StubChannel, retire_topic
from tests.delivery import delivery_task_id
from tests.integration.conftest import (
    chat_ws_url,
    in_thread,
    open_task,
    post_message,
    post_project,
    session_auth_headers,
)
from tests.integration.test_accept_pr import app_world as app_world
from tests.support.living_doc import document_of


def _doc(client, room) -> str:
    """The room's document, as its routes address it."""
    return f"/documents/{document_of(client, room)}"


@pytest.fixture
def frames(monkeypatch) -> list[tuple[str, dict]]:
    """Every frame published to any channel, in order."""
    seen: list[tuple[str, dict]] = []
    broker = get_broker()
    original = broker.publish

    async def publish(channel, frame):
        seen.append((channel, frame))
        await original(channel, frame)

    monkeypatch.setattr(broker, "publish", publish)
    return seen


def _room(client) -> tuple[str, str]:
    pid = post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    rid = client.post(
        "/topics",
        json={"project_id": pid, "title": "房间"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    return pid, rid


def _agent(pid: str, rid: str) -> dict[str, str]:
    """The credential a turn is minted: what `cheese` and `cheese api` both send."""
    return {"X-Cheese-Token": mint_scoped_token(project_id=pid, topic_id=rid)}


def _stale(frames: list[tuple[str, dict]], room: str) -> list[str]:
    return [
        frame["resource"]
        for channel, frame in frames
        if channel == room and frame.get("type") == "state"
    ]


def test_writing_the_doc_refreshes_the_doc_panel(client, frames):
    pid, rid = _room(client)
    r = client.put(
        _doc(client, rid),
        json={"content": "调查安排", "expected_version": 0},
        headers=_agent(pid, rid),
    )
    assert r.status_code == 200, r.text
    assert _stale(frames, rid) == ["doc"]


def test_opening_a_piece_of_work_refreshes_the_rooms_work_list(client, frames):
    _pid, rid = _room(client)
    open_task(client, rid, "一件活", start=False)
    assert _stale(frames, rid) == ["topics"]


def test_a_notification_about_a_room_refreshes_that_room(client, frames):
    pid, rid = _room(client)
    r = client.post(
        f"/projects/{pid}/alerts",
        json={
            "title": "看下",
            "kind": "change_alert",
            "level": "light",
            "target_handle": "alice",
            "topic_id": rid,
        },
        headers=_agent(pid, rid),
    )
    assert r.status_code == 200, r.text
    assert _stale(frames, rid) == ["notify"]


@pytest.mark.usefixtures("app_world")
def test_filing_and_correcting_a_card_refreshes_the_accept_panel(client, frames):
    pid, rid = _room(client)
    task = delivery_task_id(client, rid)
    filed = client.post(
        f"/topics/{task}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
        },
        headers=_agent(pid, str(task)),
    )
    assert filed.status_code == 200, filed.text
    assert _stale(frames, rid) == ["accept"]
    frames.clear()
    corrected = client.post(
        f"/topics/{task}/accept-card/describe",
        json={"change_subject": "chore(test): say what the card is for"},
        headers=_agent(pid, str(task)),
    )
    assert corrected.status_code == 200, corrected.text
    assert _stale(frames, rid) == ["accept"]


def _propose_feedback(client, pid: str, rid: str) -> str:
    r = client.post(
        f"/topics/{rid}/feedback-proposals",
        json={
            "title": "沙箱里 make 装不上依赖",
            "what_happened": "make 停在 could not resolve host",
            "user_said": "用户没有就这个问题说过话，以上是芝士自己观察到的",
        },
        headers=_agent(pid, rid),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["block_id"]


def test_a_proposed_feedback_card_reaches_the_open_room(client, frames):
    pid, rid = _room(client)
    block_id = _propose_feedback(client, pid, rid)
    assert _stale(frames, rid) == ["feedback"]
    # The card's own line on the timeline arrives too, the same way a question
    # the agent asks does — not only after a reload.
    assert any(
        channel == rid
        and frame.get("type") == "assistant_block"
        and frame["block"]["id"] == block_id
        for channel, frame in frames
    )


def test_sending_a_feedback_card_refreshes_the_room(client, frames):
    pid, rid = _room(client)
    block_id = _propose_feedback(client, pid, rid)
    frames.clear()
    sent = client.post(
        f"/topics/{rid}/feedback-proposals/{block_id}/accept",
        json={"title": "沙箱里 make 装不上依赖", "what_happened": "解析不了域名"},
        headers=session_auth_headers("alice"),
    )
    assert sent.status_code == 200, sent.text
    assert _stale(frames, rid) == ["feedback"]


def test_dismissing_a_feedback_card_refreshes_the_room(client, frames):
    pid, rid = _room(client)
    block_id = _propose_feedback(client, pid, rid)
    frames.clear()
    r = client.post(
        f"/topics/{rid}/feedback-proposals/{block_id}/dismiss",
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    assert _stale(frames, rid) == ["feedback"]


class _CallsATool(StubChannel):
    """A turn that makes one platform tool call and stops. The call never
    reaches the platform: this stub runs nothing, which is the point."""

    tool = "mcp__native__cheese_doc_set"
    arguments: dict = {"content": "# 实况\n"}

    def emit_turn(
        self,
        topic_id: uuid.UUID,
        prompt: str,
        reply: str,
        *,
        agent: str | None = None,
    ) -> None:
        del prompt
        self.starts(topic_id)
        self.uses(topic_id, self.tool, eid="e-tool", **self.arguments)
        self.stops(topic_id, reply)


def _turn_frames(client, tmp_path, channel: StubChannel) -> list[dict]:
    """One summoned turn on `channel`, as the room sees it."""
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([channel.runtime], channel.name),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    _, room = _room(client)
    # 芝士 answers in a 支线 of the room: that is where the turn shows.
    topic_id = in_thread(client, room, "alice")
    seen: list[dict] = []
    with client.websocket_connect(chat_ws_url(topic_id, "alice")) as ws:
        post_message(client, topic_id, "alice", {"content": "@芝士 改一下文档"})
        while True:
            frame = ws.receive_json()
            seen.append(frame)
            if frame["type"] in ("done", "error"):
                break
    return seen


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("mcp__native__cheese_doc_set", {"content": "# 实况\n"}),
        ("mcp__native__cheese_accept_request", {"task": "t", "subject": "fix: x"}),
        ("mcp__native__cheese_notify", {"title": "中期汇报"}),
    ],
)
def test_a_call_the_backend_never_received_refreshes_nothing(
    client, tmp_path, tool, arguments
):
    class _Calls(_CallsATool):
        pass

    _Calls.tool, _Calls.arguments = tool, arguments
    seen = _turn_frames(client, tmp_path, _Calls())
    # The turn ran and its step reached the room — otherwise this asserts
    # nothing at all.
    assert any(f["type"] == "event_block" for f in seen)
    assert [f for f in seen if f.get("type") == "state"] == []


def test_the_turn_still_files_its_action_card(client, tmp_path):
    """The action card for what the turn did is a separate record and stays."""

    class _Pins(_CallsATool):
        tool = "mcp__native__cheese_notify"
        arguments = {"title": "中期汇报"}

    seen = _turn_frames(client, tmp_path, _Pins())
    assert any(
        f["type"] == "event_block"
        and (f["block"].get("meta") or {}).get("action") == "notify"
        for f in seen
    )


def _is_notify_card(frame: dict) -> bool:
    return (
        frame["type"] == "event_block"
        and (frame["block"].get("meta") or {}).get("action") == "notify"
    )


def test_a_turn_announces_what_it_did_while_it_is_still_running(client, tmp_path):
    class _PinsAndKeepsGoing(StubChannel):
        def emit_turn(
            self,
            topic_id: uuid.UUID,
            prompt: str,
            reply: str,
            *,
            agent: str | None = None,
        ) -> None:
            del prompt, reply
            self.starts(topic_id)
            self.uses(
                topic_id, "mcp__native__cheese_notify", eid="e-1", title="中期汇报"
            )
            self.says(topic_id, "发好了，接着改代码")

    channel = _PinsAndKeepsGoing()
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([channel.runtime], channel.name),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    _, room = _room(client)
    topic_id = in_thread(client, room, "alice")

    async def cards() -> int:
        async with client.test_factory() as session:
            rows = await session.scalars(
                select(Block).where(Block.conversation_id == uuid.UUID(topic_id))
            )
            return sum((row.meta or {}).get("action") == "notify" for row in rows)

    with client.websocket_connect(chat_ws_url(topic_id, "alice")):
        post_message(client, topic_id, "alice", {"content": "@芝士 发个通知"})
        deadline = time.monotonic() + 5
        while asyncio.run(cards()) != 1:
            assert time.monotonic() < deadline, "the running turn announced nothing"
            time.sleep(0.05)
    retire_topic(client, room)


def test_a_turn_announces_each_kind_of_action_once(client, tmp_path):
    class _PinsTwice(StubChannel):
        def emit_turn(
            self,
            topic_id: uuid.UUID,
            prompt: str,
            reply: str,
            *,
            agent: str | None = None,
        ) -> None:
            del prompt
            self.starts(topic_id)
            self.uses(
                topic_id, "mcp__native__cheese_notify", eid="e-1", title="中期汇报"
            )
            self.uses(
                topic_id, "mcp__native__cheese_notify", eid="e-2", title="终期答辩"
            )
            self.stops(topic_id, reply)

    seen = _turn_frames(client, tmp_path, _PinsTwice())
    assert len([f for f in seen if _is_notify_card(f)]) == 1
