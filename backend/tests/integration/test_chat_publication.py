"""Explicit agent publication persists chat without starting another turn."""

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.api.deps import get_chat_service
from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import (
    in_thread,
    post_message,
    post_project,
    room_socket,
    session_auth_headers,
)


def room(client):
    project = post_project(client, json={"name": "Publication"}, owner="alice").json()[
        "data"
    ]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Work"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    # 芝士 answers in a 支线 of the channel: that is where it publishes.
    thread = in_thread(client, topic["id"], "alice")
    token = mint_scoped_token(project_id=project["id"], topic_id=thread)
    return thread, {"X-Cheese-Token": token}


def private_room(client):
    """一个成员和项目队友的私聊，外加那个队友的凭据。"""
    project = post_project(client, json={"name": "Publication"}, owner="user-1").json()[
        "data"
    ]
    topic = client.get(
        f"/projects/{project['id']}/private-chat", params={"user_handle": "user-1"}
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
    with room_socket(client, topic, "alice") as ws:
        response = publish(client, topic, headers, content)
        assert response.status_code == 200, response.text
        block = response.json()["data"]
        frame = ws.receive_json()
    assert frame == {"type": "assistant_block", "block": block}
    assert block["kind"] == "message"
    assert block["author"].startswith("cheese") and block["author"] != "alice"
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


def test_who_writes_decides_what_the_message_is(client, monkeypatch):
    """One door: an agent seated in the room publishes; a person speaks; an
    agent credential for another room, or no credential at all, writes nothing."""
    topic, headers = room(client)
    other, _ = room(client)
    monkeypatch.setattr(settings, "authz_enforce_topic_access", False)
    assert publish(client, topic, {}).status_code == 401
    assert publish(client, other, headers).status_code in (401, 403)

    published = publish(client, topic, headers, "agent words").json()["data"]
    spoken = publish(client, topic, session_auth_headers("alice"), "person words")
    assert spoken.status_code == 200, spoken.text
    said = spoken.json()["data"]
    assert said["author"] == "alice"
    assert published["author"] != "alice"
    history = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
    assert [b["content"] for b in history if b["kind"] == "message"] == [
        "agent words",
        "person words",
    ]


@pytest.mark.parametrize("content", ["", "  \n  "])
def test_blank_message_is_rejected(client, content):
    topic, headers = room(client)
    assert publish(client, topic, headers, content).status_code in (400, 422)


def test_raw_terminal_output_never_publishes_even_after_stop(client, stub_hooks):
    topic, _ = room(client)
    stub_hooks.reply = "This terminal output must remain in activity."
    with room_socket(client, topic, "alice") as ws:
        post_message(client, topic, "alice", {"content": "@芝士 检查一下"})
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


def test_a_private_chat_only_shows_what_chat_send_sent(client, stub_hooks):
    """私聊和房间共用一条发布路径 (结论 19).

    A private chat used to publish its own terminal reply, which is why its
    agent was told not to call chat_send there. Two rooms, two publication
    rules and two prompts were one product behaviour with two implementations.
    """
    topic, headers = private_room(client)
    stub_hooks.reply = "这段是终端里的最终答复。"
    with room_socket(client, topic, "user-1") as ws:
        post_message(client, topic, "user-1", {"content": "@芝士 帮我记一下偏好"})
        frames = []
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] in ("done", "error"):
                break
    assert not any(frame["type"] == "assistant_block" for frame in frames)

    def timeline():
        history = client.get(f"/topics/{topic}/blocks", headers=headers)
        assert history.status_code == 200, history.text
        return history.json()["data"]["data"]

    def ai_messages(blocks):
        return [
            block["content"]
            for block in blocks
            if block["kind"] == "message" and block["author"].startswith("cheese")
        ]

    # Nothing published, nothing in the room — and the reply is not lost, it is
    # in activity where a room's terminal output goes. One read answers both:
    # between two reads the room could have changed under the assertions.
    after_turn = timeline()
    assert ai_messages(after_turn) == []
    assert [
        block["content"]
        for block in after_turn
        if block["kind"] == "event" and block["content"] == stub_hooks.reply
    ]

    # One chat_send, exactly one message.
    sent = publish(client, topic, headers, "记好了，偏好写进文档了。")
    assert sent.status_code == 200, sent.text
    assert ai_messages(timeline()) == ["记好了，偏好写进文档了。"]


def test_publish_during_work_keeps_the_turn_open(client, stub_hooks, monkeypatch):
    topic, headers = room(client)
    raw_text = "Internal investigation detail"

    def begin(topic_id, prompt, reply, agent=None):
        stub_hooks.starts(topic_id)
        stub_hooks.acknowledges(topic_id, prompt)
        stub_hooks.says(topic_id, raw_text)

    monkeypatch.setattr(stub_hooks, "emit_turn", begin)
    with room_socket(client, topic, "alice") as ws:
        post_message(client, topic, "alice", {"content": "@芝士 检查一下"})
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
        client.portal.call(stub_hooks.stops, uuid.UUID(topic), raw_text)
        while ws.receive_json()["type"] != "done":
            pass


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
@pytest.mark.parametrize(
    ("make_room", "speaker"),
    [(room, "alice"), (private_room, "user-1")],
    ids=["room", "private"],
)
def test_silence_reminder_only_queues_for_an_active_silent_response(
    client, stub_hooks, monkeypatch, threshold, make_room, speaker
):
    """一条发布路径 (结论 19) means one silence rule as well.

    A private chat is exempted from nothing here: now that its reply reaches the
    room only through chat_send, a silent private chat is exactly the room that
    shows nothing while someone waits.
    """
    from app.domain.agent import chat as chat_module
    from app.domain.agent.room import turn as turn_module
    from app.domain.agent.turn.intake import assistant as assistant_module

    topic, headers = make_room(client)
    chat = client.app.dependency_overrides[get_chat_service]()
    clock = datetime.now(UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock

    monkeypatch.setattr(chat_module, "datetime", Clock)
    monkeypatch.setattr(turn_module, "datetime", Clock)
    monkeypatch.setattr(assistant_module, "datetime", Clock)
    assert settings.chat_progress_reminder_after_s == 600
    monkeypatch.setattr(settings, "chat_progress_reminder_after_s", threshold)
    system_event = AsyncMock(wraps=chat.post_system_event)
    monkeypatch.setattr(chat, "post_system_event", system_event)

    def begin(topic_id, prompt, reply, agent=None):
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
    with room_socket(client, topic, speaker) as ws:
        post_message(client, topic, speaker, {"content": "@芝士 检查一下"})
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


def test_publication_from_a_remote_executor_still_counts_as_speaking(
    client, stub_hooks, monkeypatch
):
    """The publish endpoint attributes a publication through the runner's live
    work, which only knows turns THIS process executes. A remote executor's
    turn publishes over HTTP with turn_id=None — yet its turn is exactly the
    one the silence sweep is tracking (`live.active_turn_ids`). The publication
    must still refresh `last_chat_at`, or the sweep keeps "reminding" a turn
    that just spoke, counting the silence from turn start."""
    from app.api.deps import get_work_runner
    from app.domain.agent import chat as chat_module
    from app.domain.agent.room import turn as turn_module
    from app.domain.agent.turn.intake import assistant as assistant_module

    topic, headers = room(client)
    chat = client.app.dependency_overrides[get_chat_service]()
    clock = datetime.now(UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock

    monkeypatch.setattr(chat_module, "datetime", Clock)
    monkeypatch.setattr(turn_module, "datetime", Clock)
    monkeypatch.setattr(assistant_module, "datetime", Clock)
    threshold = 90
    monkeypatch.setattr(settings, "chat_progress_reminder_after_s", threshold)

    def begin(topic_id, prompt, reply, agent=None):
        stub_hooks.starts(topic_id)
        stub_hooks.acknowledges(topic_id, prompt)
        stub_hooks.says(topic_id, "Internal output")

    monkeypatch.setattr(stub_hooks, "emit_turn", begin)
    notices = []

    async def record_notice(topic_id, notice):
        notices.append(notice)
        return True

    monkeypatch.setattr(chat, "notify_running_turn", record_notice)
    # The executor is remote: this process runs no work for the topic, so the
    # endpoint's runner lookup attributes nothing.
    monkeypatch.setattr(get_work_runner(), "live_work_for_topic", lambda _t: None)
    with room_socket(client, topic, "alice") as ws:
        post_message(client, topic, "alice", {"content": "@芝士 检查一下"})
        while True:
            frame = ws.receive_json()
            if (
                frame["type"] == "event_block"
                and frame["block"]["content"] == "Internal output"
            ):
                break
        # A second agent's turn is live in the same room, and IT is the one
        # holding the topic's active slot. Attribution must still land on the
        # PUBLISHER's own turn — crediting this publication to the other
        # agent's turn would silence ITS reminder while this room stays dark.
        import dataclasses

        clock += timedelta(seconds=threshold)
        own = next(
            s for (t, _), s in chat.live.hook_work.items() if t == uuid.UUID(topic)
        )
        rival_id = uuid.uuid4()
        rival = dataclasses.replace(
            own, work_id=rival_id, acting_agent="cheese-other", started_at=clock
        )
        chat.live.hook_work[(uuid.UUID(topic), rival_id)] = rival
        chat.live.active_turn_ids[uuid.UUID(topic)] = {rival_id}

        sent = publish(client, topic, headers).json()["data"]
        assert next_block(ws)["id"] == sent["id"]
        assert sent["turn_id"] is None  # the endpoint could not attribute it
        # The publication still counts as the publisher's turn speaking: no
        # reminder right after it, and none inside a fresh interval either.
        assert own.last_chat_at == clock
        assert rival.last_chat_at is None  # never credited across agents
        assert client.portal.call(chat.remind_silent_turns) == 0
        clock += timedelta(seconds=threshold - 1)
        assert client.portal.call(chat.remind_silent_turns) == 0
        assert notices == []


def test_publication_attribution_never_guesses_between_agents():
    """Two live turns, neither the publisher's: attribute nothing.

    The fallback exists so a remote executor's publication refreshes ITS
    turn. Between two agents whose turns are both live in one room, a
    publication from a third party matches nobody, and a wrong credit is
    worse than none: it silences the reminder of a turn that never spoke.
    """
    from types import SimpleNamespace

    from app.domain.agent.turn.state.live import LiveWork

    topic = uuid.uuid4()
    a, b = uuid.uuid4(), uuid.uuid4()
    live = LiveWork()
    live.hook_work = {
        (topic, a): SimpleNamespace(work_id=a, acting_agent="cheese-a"),
        (topic, b): SimpleNamespace(work_id=b, acting_agent="cheese-b"),
    }
    live.active_turn_ids = {topic: {a}}
    attribute = LiveWork.attributed_work_id
    # A third party publishes: two live turns, neither theirs — no guess.
    assert attribute(live, topic, "cheese-c") is None
    # The publisher's own turn wins even when it is not the active one.
    assert attribute(live, topic, "cheese-b") == b
    # Both live turns are the publisher's: the active one breaks the tie.
    live.hook_work[(topic, b)] = SimpleNamespace(work_id=b, acting_agent="cheese-a")
    assert attribute(live, topic, "cheese-a") == a
    # One live turn only: unambiguous whoever publishes (a token naming no
    # agent seat resolves to the room's roster, which may differ).
    live.hook_work = {(topic, a): SimpleNamespace(work_id=a, acting_agent="cheese-a")}
    assert attribute(live, topic, "cheese-c") == a
    # Nothing live: nothing to attribute.
    live.hook_work = {}
    assert attribute(live, topic, "cheese-a") is None


def test_consuming_work_id_only_delivers_when_unambiguous():
    """An inbound message goes to exactly one live turn, never a guess.

    Today the active set holds at most one id and this reduces to the old
    single-slot behavior, including its tolerance of a missing hook state.
    With several turns live (parallel agents in one room), delivery needs
    exactly one recipient match — zero or two both hold the message.
    """
    from types import SimpleNamespace

    from app.domain.agent.turn.state.live import LiveWork

    topic = uuid.uuid4()
    a, b = uuid.uuid4(), uuid.uuid4()
    live = LiveWork()
    live.hook_work = {
        (topic, a): SimpleNamespace(work_id=a, agent_instance_handle="inst-a"),
        (topic, b): SimpleNamespace(work_id=b, agent_instance_handle="inst-b"),
    }
    live.active_turn_ids = {topic: {a, b}}
    consume = LiveWork.consuming_work_id
    to = lambda handle: lambda s: s.agent_instance_handle == handle  # noqa: E731
    # Exactly one match: delivered there.
    assert consume(live, topic, to("inst-a")) == a
    assert consume(live, topic, to("inst-b")) == b
    # No match, or both match: held rather than guessed.
    assert consume(live, topic, to("inst-c")) is None
    assert consume(live, topic, lambda s: True) is None
    # No matcher and several live: held.
    assert consume(live, topic) is None
    # A live id without hook state is tolerated (single-slot compatibility)…
    live.active_turn_ids = {topic: {a, b}}
    del live.hook_work[(topic, b)]
    assert consume(live, topic, to("inst-a")) is None  # b still could be it
    # …unless the caller is strict: then a missing state cannot receive.
    live.active_turn_ids = {topic: {b}}
    assert consume(live, topic, to("inst-b"), strict=True) is None
    assert consume(live, topic, to("inst-b"), strict=False) == b
    # One live turn with its state: the everyday case.
    live.hook_work[(topic, b)] = SimpleNamespace(
        work_id=b, agent_instance_handle="inst-b"
    )
    assert consume(live, topic, to("inst-b")) == b
    assert consume(live, topic) == b
