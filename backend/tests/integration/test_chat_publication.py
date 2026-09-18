"""Explicit agent publication persists chat without starting another turn."""

import asyncio
import importlib.util
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.api.deps import get_chat_service
from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import chat_ws_url, session_auth_headers


def room(client):
    project = client.post("/projects", json={"name": "Publication"}).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Work", "created_by": "alice"},
    ).json()["data"]
    token = mint_scoped_token(project_id=project["id"], topic_id=topic["id"])
    return topic["id"], {"X-Cheese-Token": token}


def publish(client, topic, headers, content="我先核对当前流程。", **extra):
    return client.post(
        f"/topics/{topic}/messages",
        json={"content": content, "request_id": str(uuid.uuid4()), **extra},
        headers=headers,
    )


@pytest.mark.parametrize(
    "content", ["我先核对当前流程。", "I will check the current flow."]
)
def test_publication_is_durable_live_and_does_not_wake_model(
    client, stub_hooks, content
):
    topic, headers = room(client)
    with client.websocket_connect(chat_ws_url(topic, "alice")) as ws:
        response = publish(client, topic, headers, content)
        assert response.status_code == 200, response.text
        block = response.json()["data"]
        frame = ws.receive_json()
    assert frame == {"type": "assistant_block", "block": block}
    assert block["kind"] == "message"
    assert block["author_type"] == "ai"
    assert block["author"] != "alice"
    assert block["content"] == content
    assert (block.get("meta") or {}).get("in_room") is not False
    assert stub_hooks.last_prompt is None
    history = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
    assert any(
        b["id"] == block["id"] and b["content"] == block["content"] for b in history
    )


def test_retry_returns_same_message_and_changed_body_conflicts(client):
    topic, headers = room(client)
    request_id = str(uuid.uuid4())
    first = publish(client, topic, headers, request_id=request_id)
    assert first.status_code == 200, first.text
    second = publish(client, topic, headers, request_id=request_id)
    assert second.json()["data"]["id"] == first.json()["data"]["id"]
    changed = publish(client, topic, headers, "正文变了", request_id=request_id)
    assert changed.status_code == 409
    history = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
    assert len([b for b in history if b["kind"] == "message"]) == 1


def test_concurrent_retries_persist_one_message(client):
    topic, headers = room(client)
    request_id = str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [
            pool.submit(publish, client, topic, headers, request_id=request_id)
            for _ in range(2)
        ]
        responses = [request.result(timeout=10) for request in requests]
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json()["data"]["id"] == responses[1].json()["data"]["id"]
    history = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
    assert len([b for b in history if b["kind"] == "message"]) == 1


def test_reply_preserves_reference_and_rejects_another_room(client):
    topic, headers = room(client)
    first = publish(client, topic, headers).json()["data"]
    reply = publish(client, topic, headers, "已经查到了。", reply_to=first["id"])
    assert reply.status_code == 200, reply.text
    assert reply.json()["data"]["reply_to"] == first["id"]
    other, other_headers = room(client)
    assert (
        publish(client, other, other_headers, reply_to=first["id"]).status_code == 422
    )


def test_only_authenticated_in_scope_agents_can_publish(client, monkeypatch):
    topic, headers = room(client)
    other, _ = room(client)
    monkeypatch.setattr(settings, "authz_enforce_topic_access", False)
    assert publish(client, topic, {}).status_code == 403
    assert publish(client, topic, session_auth_headers("alice")).status_code == 403
    assert publish(client, other, headers).status_code == 403


@pytest.mark.parametrize("content", ["", "  \n  "])
def test_blank_message_is_rejected(client, content):
    topic, headers = room(client)
    assert publish(client, topic, headers, content).status_code in (400, 422)


def test_cli_file_send_uses_the_publication_route(
    client, monkeypatch, tmp_path, capsys
):
    topic, headers = room(client)
    loader = SourceFileLoader(
        "publication_cli", str(Path(__file__).parents[2] / "sandbox/cheese")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    cli = importlib.util.module_from_spec(spec)
    loader.exec_module(cli)
    message = tmp_path / "update.txt"
    message.write_text("检查通过了。\n`$HOME` 和 $(echo hi) 是原文。", encoding="utf-8")
    monkeypatch.setattr(cli, "TOPIC", topic)
    monkeypatch.setattr(
        cli.sys, "argv", ["cheese", "chat", "send", "--file", str(message)]
    )

    def request(method, path, body):
        response = client.request(method, path, json=body, headers=headers)
        assert response.status_code == 200, response.text
        return response.json()

    monkeypatch.setattr(cli, "_call", request)
    cli.main()
    block = json.loads(capsys.readouterr().out)
    assert block["content"] == message.read_text(encoding="utf-8")
    assert block["kind"] == "message"


def test_raw_terminal_output_never_publishes_even_after_stop(client, stub_hooks):
    topic, _ = room(client)
    stub_hooks.reply = "This terminal output must remain in activity."
    with client.websocket_connect(chat_ws_url(topic, "alice")) as ws:
        ws.send_json({"type": "message", "content": "检查一下", "summon": True})
        frames = []
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] in ("done", "error"):
                break
    assert not any(frame["type"] == "assistant_block" for frame in frames)
    history = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
    output = [block for block in history if block["content"] == stub_hooks.reply]
    assert output and all(
        b["kind"] == "event" and b["meta"]["in_room"] is False for b in output
    )
    assert "chat_send" in stub_hooks.last_system_prompt
    assert "chat_send" in stub_hooks.last_prompt


def test_publish_during_work_keeps_turn_open_and_only_published_text_enters_memory(
    client, stub_hooks, monkeypatch
):
    topic, headers = room(client)
    raw_text = "Internal investigation detail"
    memories = []
    chat = client.app.dependency_overrides[get_chat_service]()
    monkeypatch.setattr(
        chat, "_schedule_memory_extraction", lambda **kwargs: memories.append(kwargs)
    )

    def begin(topic_id, prompt, reply):
        stub_hooks.starts(topic_id)
        stub_hooks.acknowledges(topic_id, prompt)
        stub_hooks.says(topic_id, raw_text)

    monkeypatch.setattr(stub_hooks, "emit_turn", begin)
    with client.websocket_connect(chat_ws_url(topic, "alice")) as ws:
        ws.send_json({"type": "message", "content": "检查一下", "summon": True})
        turn_id = None
        while True:
            frame = ws.receive_json()
            if frame["type"] == "turn_started":
                turn_id = frame["turn_id"]
            if frame["type"] == "event_block" and frame["block"]["content"] == raw_text:
                break
        assert turn_id is not None
        first = publish(client, topic, headers).json()["data"]
        assert first["turn_id"] == turn_id
        assert next_frame(ws, "assistant_block") == {
            "type": "assistant_block",
            "block": first,
        }
        second = publish(client, topic, headers, "检查通过了。").json()["data"]
        assert second["turn_id"] == turn_id
        assert next_frame(ws, "assistant_block") == {
            "type": "assistant_block",
            "block": second,
        }
        assert memories == []
        client.portal.call(stub_hooks.stops, uuid.UUID(topic), raw_text)
        while ws.receive_json()["type"] != "done":
            pass
    assert len(memories) == 1
    assert (
        memories[0]["assistant_text"] == first["content"] + "\n\n" + second["content"]
    )


def next_frame(ws, kind):
    """The next frame of that kind.

    The room's socket carries more than publications: a session's control state
    arrives on it too, and whether one of those lands between two publications
    is a matter of timing. Reading the very next frame and expecting it to be
    the publication is what made this file fail about one run in three.
    """
    while True:
        frame = ws.receive_json()
        if frame["type"] == kind:
            return frame


def next_block(ws):
    """The block carried by the next frame that carries one."""
    while True:
        frame = ws.receive_json()
        if "block" in frame:
            return frame["block"]


@pytest.mark.parametrize("threshold", [600, 90])
def test_silence_reminder_only_queues_for_an_active_silent_response(
    client, stub_hooks, monkeypatch, threshold
):
    from app.domain.agent import chat as chat_module

    topic, headers = room(client)
    chat = client.app.dependency_overrides[get_chat_service]()
    clock = datetime.now(UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock

    monkeypatch.setattr(chat_module, "datetime", Clock)
    assert settings.chat_progress_reminder_after_s == 600
    monkeypatch.setattr(settings, "chat_progress_reminder_after_s", threshold)
    system_event = AsyncMock(wraps=chat.post_system_event)
    monkeypatch.setattr(chat, "post_system_event", system_event)

    def begin(topic_id, prompt, reply):
        stub_hooks.starts(topic_id)
        stub_hooks.acknowledges(topic_id, prompt)
        stub_hooks.says(topic_id, "Internal output")

    monkeypatch.setattr(stub_hooks, "emit_turn", begin)
    release = asyncio.Event()
    started = asyncio.Event()
    notices = []

    async def delayed_notice(topic_id, notice):
        notices.append(notice)
        started.set()
        await release.wait()
        return True

    monkeypatch.setattr(chat, "notify_running_turn", delayed_notice)
    with client.websocket_connect(chat_ws_url(topic, "alice")) as ws:
        ws.send_json({"type": "message", "content": "检查一下", "summon": True})
        while True:
            frame = ws.receive_json()
            if (
                frame["type"] == "event_block"
                and frame["block"]["content"] == "Internal output"
            ):
                break
        assert client.portal.call(chat.remind_silent_turns) == 0
        system_event.reset_mock()
        clock += timedelta(seconds=threshold - 1)
        assert client.portal.call(chat.remind_silent_turns) == 0
        clock += timedelta(seconds=1)
        client.portal.call(stub_hooks.says, uuid.UUID(topic), "More internal output")
        assert next_block(ws)["content"] == "More internal output"
        sweep = client.portal.start_task_soon(chat.remind_silent_turns)
        client.portal.call(started.wait)
        assert not sweep.done()
        system_event.assert_not_called()
        client.portal.call(release.set)
        assert sweep.result(timeout=2) == 1
        assert len(notices) == 1 and "chat_send" in notices[0]
        assert "Ignore this only if the turn is already finished" in notices[0]
        # Inside the interval there is one reminder, not a stream of them.
        clock += timedelta(seconds=threshold - 1)
        assert client.portal.call(chat.remind_silent_turns) == 0
        # Past it again with still nothing published, the silence is reminded
        # about again. Being asked once and then left alone is what let a turn
        # work for hours while the room showed nothing.
        clock += timedelta(seconds=1)
        assert client.portal.call(chat.remind_silent_turns) == 1
        assert len(notices) == 2
        request_id = str(uuid.uuid4())
        sent = publish(client, topic, headers, request_id=request_id).json()["data"]
        assert next_block(ws)["id"] == sent["id"]
        assert client.portal.call(chat.remind_silent_turns) == 0
        clock += timedelta(seconds=threshold)
        # Replaying a previous send must not masquerade as a fresh update.
        assert publish(client, topic, headers, request_id=request_id).status_code == 200
        assert next_block(ws)["id"] == sent["id"]
        assert client.portal.call(chat.remind_silent_turns) == 1
        assert len(notices) == 3
        system_event.assert_not_called()
        # Stop must disarm a fresh silence interval, not merely a sent reminder.
        sent = publish(client, topic, headers, content="检查已经结束。").json()["data"]
        assert next_block(ws)["id"] == sent["id"]
        client.portal.call(stub_hooks.stops, uuid.UUID(topic), "Finished internally")
        while ws.receive_json()["type"] != "done":
            pass
        clock += timedelta(seconds=threshold)
        assert client.portal.call(chat.remind_silent_turns) == 0
        assert len(notices) == 3
