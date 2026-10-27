"""Explicit agent publication persists chat without starting another turn."""

import asyncio
import base64
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update

from app.api.deps import get_chat_service
from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.turn.steps.reminders import QuietSessionReminders
from app.domain.block.models import Block, BlockKind
from app.domain.delivery.models import NativeInput
from app.domain.event_author import AuthorType
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
    return thread, {"Authorization": f"Bearer {token}"}


def private_room(client):
    """一个成员和项目队友的私聊，外加那个队友的凭据。"""
    project = post_project(client, json={"name": "Publication"}, owner="user-1").json()[
        "data"
    ]
    topic = client.get(
        f"/projects/{project['id']}/private-chat", params={"user_handle": "user-1"}
    ).json()["data"]
    token = mint_scoped_token(project_id=project["id"], topic_id=topic["id"])
    return topic["id"], {"Authorization": f"Bearer {token}"}


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
    preview = reply.json()["data"]["reply_preview"]
    assert preview["id"] == first["id"]
    assert preview["author"] == first["author"]
    assert preview["content"] == first["content"]
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
    assert not [block for block in history if block["content"] == stub_hooks.reply]
    records = client.get(f"/topics/{topic}/session-records").json()["data"]["items"]
    assert any(stub_hooks.reply in str(item["record"]) for item in records)
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
    records = client.get(f"/topics/{topic}/session-records", headers=headers).json()[
        "data"
    ]["items"]
    assert any(stub_hooks.reply in str(item["record"]) for item in records)

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
            if frame["type"] == "turn_started":
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


def _read_by_its_session(client, topic: str) -> datetime:
    """Wait for the session to read what it was handed (its echo is the read),
    and return the moment after: the clock the reminders measure from."""

    async def read() -> bool:
        async with client.test_factory() as session:
            return (
                await session.scalar(
                    select(NativeInput.id).where(
                        NativeInput.conversation_id == uuid.UUID(topic),
                        NativeInput.echoed_at.is_not(None),
                    )
                )
                is not None
            )

    deadline = datetime.now(UTC) + timedelta(seconds=10)
    while not client.portal.call(read):
        assert datetime.now(UTC) < deadline, "the session never read its input"
    return datetime.now(UTC)


def _stopped_working(chat, topic) -> None:
    """Until the session's runner has said it is idle: the room hears the
    result (``done``) a moment before the seat is marked idle."""
    deadline = time.monotonic() + 10
    while chat.compute.working_seat(uuid.UUID(str(topic))) is not None:
        assert time.monotonic() < deadline, "the session never went idle"
        time.sleep(0.01)


def _said_at(client, block_id: str, at: datetime) -> None:
    """The message ``block_id`` was written at ``at``, on the sweep's clock."""

    async def move() -> None:
        async with client.test_factory() as session:
            await session.execute(
                update(Block)
                .where(Block.id == uuid.UUID(block_id))
                .values(created_at=at)
            )
            await session.commit()

    client.portal.call(move)


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
    shows nothing while someone waits. What is measured: the session at work
    read a person's message, and has published nothing since. Once it has
    answered, it is not prodded to narrate the rest of its work.
    """
    topic, headers = make_room(client)
    chat = client.app.dependency_overrides[get_chat_service]()
    quiet = QuietSessionReminders(chat, chat.compute)
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

    async def delayed_notice(topic_id, notice, **_kwargs):
        notices.append(notice)
        started.set()
        await release.wait()
        return True

    monkeypatch.setattr(chat, "notify_running_session", delayed_notice)
    with room_socket(client, topic, speaker) as ws:
        post_message(client, topic, speaker, {"content": "@芝士 检查一下"})
        while True:
            frame = ws.receive_json()
            if frame["type"] == "turn_started":
                break
        clock = _read_by_its_session(client, topic)
        assert client.portal.call(quiet.silence, clock) == 0
        system_event.reset_mock()
        clock += timedelta(seconds=threshold - 1)
        assert client.portal.call(quiet.silence, clock) == 0
        clock += timedelta(seconds=1)
        client.portal.call(stub_hooks.says, uuid.UUID(topic), "More internal output")
        sweep = client.portal.start_task_soon(quiet.silence, clock)
        client.portal.call(started.wait)
        assert not sweep.done()
        system_event.assert_not_called()
        client.portal.call(release.set)
        assert sweep.result(timeout=2) == 1
        assert len(notices) == 1 and "chat_send" in notices[0]
        assert "已经停下的话才不用理会" in notices[0]
        # Inside the interval there is one reminder, not a stream of them.
        clock += timedelta(seconds=threshold - 1)
        assert client.portal.call(quiet.silence, clock) == 0
        # Past it again with the person still unanswered, it is reminded again.
        clock += timedelta(seconds=1)
        assert client.portal.call(quiet.silence, clock) == 1
        assert len(notices) == 2
        sent = publish(client, topic, headers).json()["data"]
        assert next_block(ws)["id"] == sent["id"]
        _said_at(client, sent["id"], clock)
        # It answered: from here it works quietly, however long the work takes.
        # The process shows in 现场 and the board, not in the conversation.
        for _ in range(3):
            clock += timedelta(seconds=threshold)
            assert client.portal.call(quiet.silence, clock) == 0
        assert len(notices) == 2
        system_event.assert_not_called()
        client.portal.call(stub_hooks.stops, uuid.UUID(topic), "Finished internally")
        while ws.receive_json()["type"] != "done":
            pass
        _stopped_working(chat, topic)


def test_another_agents_words_are_not_this_ones(client, stub_hooks, monkeypatch):
    """Two teammates in one room have two silences: one of them speaking does
    not stop the reminder the other is owed."""
    topic, _headers = room(client)
    chat = client.app.dependency_overrides[get_chat_service]()
    quiet = QuietSessionReminders(chat, chat.compute)
    threshold = 90
    monkeypatch.setattr(settings, "chat_progress_reminder_after_s", threshold)

    def begin(topic_id, prompt, reply, agent=None):
        stub_hooks.starts(topic_id)
        stub_hooks.acknowledges(topic_id, prompt)
        stub_hooks.says(topic_id, "Internal output")

    monkeypatch.setattr(stub_hooks, "emit_turn", begin)
    notices = []

    async def record_notice(topic_id, notice, **_kwargs):
        notices.append(notice)
        return True

    monkeypatch.setattr(chat, "notify_running_session", record_notice)
    with room_socket(client, topic, "alice") as ws:
        post_message(client, topic, "alice", {"content": "@芝士 检查一下"})
        while True:
            frame = ws.receive_json()
            if frame["type"] == "turn_started":
                break
        _read_by_its_session(client, topic)

        # The other teammate speaks half a window before the clock below: had
        # its words counted as this seat's, this seat would not be due yet.
        now = datetime.now(UTC)

        async def another_agent_says() -> None:
            async with client.test_factory() as session:
                project = await session.scalar(
                    select(Block.project_id).where(
                        Block.conversation_id == uuid.UUID(topic)
                    )
                )
                session.add(
                    Block(
                        project_id=project,
                        conversation_id=uuid.UUID(topic),
                        author="cheese-0ther0agent0",
                        author_type=AuthorType.participant,
                        kind=BlockKind.message,
                        content="我这边好了",
                        created_at=now + timedelta(seconds=threshold // 2),
                    )
                )
                await session.commit()

        client.portal.call(another_agent_says)
        clock = now + timedelta(seconds=threshold + 1)
        assert client.portal.call(quiet.silence, clock) == 1
        assert len(notices) == 1


PNG = b"\x89PNG\r\n\x1a\n" + b"pixels"


def picture(name, raw=PNG):
    """One attachment the way `chat_send` sends it: bytes, not a room path."""
    return {
        "path": f"/executor/{name}",
        "name": name,
        "content_b64": base64.b64encode(raw).decode("ascii"),
    }


def test_an_agent_publishes_a_picture_in_the_conversation(client, stub_hooks):
    """The bytes arrive with the message, land under this room's `uploads/`,
    and the reader gets both blocks — the words, then the picture."""
    topic, headers = room(client)
    with room_socket(client, topic, "alice") as ws:
        response = publish(
            client, topic, headers, "看这一张", attachments=[picture("shot.png")]
        )
        assert response.status_code == 200, response.text
        frames = []
        # Bounded: a missing broadcast must fail here, not block until the
        # test's own wall clock runs out.
        for _ in range(20):
            frame = ws.receive_json()
            if frame["type"] == "assistant_block":
                frames.append(frame)
                if len(frames) == 2:
                    break
    assert len(frames) == 2, frames
    assert [frame["block"]["kind"] for frame in frames] == ["message", "attachment"]
    assert frames[0]["block"]["content"] == "看这一张"
    shown = frames[1]["block"]
    assert shown["content"].startswith("uploads/")
    assert shown["content"].endswith("/shot.png")
    assert shown["mime_type"] == "image/png"
    served = client.get(
        f"/topics/{topic}/attachments/raw", params={"path": shown["content"]}
    )
    assert served.status_code == 200
    assert served.content == PNG
    history = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
    assert [b["kind"] for b in history if b["kind"] in ("message", "attachment")] == [
        "message",
        "attachment",
    ]


def test_a_picture_alone_is_a_message(client, stub_hooks):
    """Pictures need no caption: an empty content with attachments is a send."""
    topic, headers = room(client)
    response = client.post(
        f"/topics/{topic}/messages",
        json={"request_id": str(uuid.uuid4()), "attachments": [picture("shot.png")]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["kind"] == "message"
    history = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
    assert [b["kind"] for b in history if b["kind"] == "attachment"] == ["attachment"]


def test_only_pictures_go_inline(client):
    """A file that is not one of the four raster pictures stays out of the
    bubble — and neither the message nor a byte of the file is stored."""
    topic, project_id, headers = room_with_project(client)
    response = publish(
        client,
        topic,
        headers,
        "这是报告",
        attachments=[picture("report.png", raw=b"%PDF-1.7 report")],
    )
    assert response.status_code == 422
    assert uploads_under(project_id) == []
    history = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
    assert [b for b in history if b["kind"] == "message"] == []


def uploads_under(project_id):
    """The bytes this project's rooms hold under `uploads/` — where a
    publication's pictures land. The room file list hides them on purpose, so
    a test that wants to count copies has to look at the tree."""
    root = Path(settings.workspace_root) / ".room-files" / project_id
    return sorted(p for p in root.rglob("uploads/*/*") if p.is_file())


def room_with_project(client):
    project = post_project(client, json={"name": "Publication"}, owner="alice").json()[
        "data"
    ]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Work"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    thread = in_thread(client, topic["id"], "alice")
    token = mint_scoped_token(project_id=project["id"], topic_id=thread)
    return thread, project["id"], {"Authorization": f"Bearer {token}"}


def test_a_retry_of_a_picture_keeps_one_copy(client, stub_hooks):
    """The same request_id after a lost response returns the stored message
    and writes no second copy of the bytes: a retry is not a new send."""
    topic, project_id, headers = room_with_project(client)
    request_id = str(uuid.uuid4())
    first = publish(
        client,
        topic,
        headers,
        "看这一张",
        request_id=request_id,
        attachments=[picture("shot.png")],
    )
    assert first.status_code == 200, first.text
    before = uploads_under(project_id)
    assert len(before) == 1
    retry = publish(
        client,
        topic,
        headers,
        "看这一张",
        request_id=request_id,
        attachments=[picture("shot.png")],
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["data"]["id"] == first.json()["data"]["id"]
    assert uploads_under(project_id) == before
    history = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
    assert [b["kind"] for b in history if b["kind"] in ("message", "attachment")] == [
        "message",
        "attachment",
    ]


def test_a_retry_with_a_different_picture_conflicts(client, stub_hooks):
    """The same id with another picture is another publication: it must be
    refused, not silently answered with the old message."""
    topic, project_id, headers = room_with_project(client)
    request_id = str(uuid.uuid4())
    first = publish(
        client,
        topic,
        headers,
        "看这一张",
        request_id=request_id,
        attachments=[picture("shot.png")],
    )
    assert first.status_code == 200, first.text
    changed = publish(
        client,
        topic,
        headers,
        "看这一张",
        request_id=request_id,
        attachments=[picture("shot.png", raw=PNG + b"changed")],
    )
    assert changed.status_code == 409
    assert len(uploads_under(project_id)) == 1


def test_a_bad_second_picture_writes_nothing(client):
    """Every picture is checked before any of them is written: the good first
    one must not be left behind by the bad second one."""
    topic, project_id, headers = room_with_project(client)
    response = publish(
        client,
        topic,
        headers,
        "看这两张",
        attachments=[picture("ok.png"), picture("bad.png", raw=b"%PDF-1.7 report")],
    )
    assert response.status_code == 422
    assert uploads_under(project_id) == []
    history = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
    assert [b for b in history if b["kind"] == "message"] == []
