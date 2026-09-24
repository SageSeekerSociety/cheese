"""Card instructions reach the observed native parent, without a card session.

An unknown parent leaves committed intent waiting. A known parent receives the
specific child identity; delayed hooks and replacement workers cannot retarget
an existing instruction. The hook transport is substituted, not the database,
HTTP route, hook translator, runner or runtime admission path.
"""

import asyncio
import uuid

from sqlalchemy import select

from app.domain.delivery.models import Delivery
from app.domain.identity.handles import looks_like_agent_handle
from tests.conftest import wait_work_idle as _wait_work_idle
from tests.integration.conftest import chat_ws_url, post_project


def _project(client) -> dict:
    return post_project(client, json={"name": "P", "owner_handle": "user-1"}).json()[
        "data"
    ]


def _room(client, project_id: str) -> dict:
    return client.post(
        "/topics",
        json={"project_id": project_id, "title": "大话题", "created_by": "user-1"},
    ).json()["data"]


def _thread(client, room_id: str, title: str = "子活") -> dict:
    return client.post(
        f"/topics/{room_id}/split",
        json=dict(reviewer_handle="alice", **{"title": title}),
    ).json()["data"]


def _record_screens(stub_hooks) -> list[str]:
    """每一次「起一块屏幕」的 topic id。起屏幕就是起容器，这是唯一看得见它的地方。"""
    seen: list[str] = []
    original = stub_hooks.ensure_ready

    async def _spy(**kw):
        seen.append(str(kw["session"].topic_id))
        return await original(**kw)

    stub_hooks.ensure_ready = _spy
    return seen


def _drain_until_done(ws) -> list[dict]:
    frames: list[dict] = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] in ("done", "error"):
            break
    return frames


def _blocks(client, place_id: str) -> list[dict]:
    return client.get(f"/topics/{place_id}/blocks").json()["data"]["data"]


def _card_blocks(client, room_id: str, task_id: str) -> list[dict]:
    r = client.get(f"/topics/{room_id}/tasks/{task_id}")
    assert r.status_code == 200, r.text
    return r.json()["data"]["blocks"]


def _say_on_card(client, room_id: str, task_id: str, content: str):
    return client.post(
        f"/topics/{room_id}/tasks/{task_id}/messages",
        json={"content": content, "author": "user-1"},
    )


def test_writing_on_a_thread_wakes_the_room_to_relay_it(client, stub_hooks):
    p = _project(client)
    room = _room(client, p["id"])
    thread = _thread(client, room["id"])
    _wait_work_idle()

    # The real hook translator and persistence observe which native parent
    # owns this worker before a human addresses it.
    emit = stub_hooks.emit_turn

    def start_child(topic_id, prompt, reply):
        stub_hooks.starts(topic_id)
        stub_hooks.acknowledges(topic_id, prompt)
        stub_hooks.spawns(topic_id, thread_label=thread["thread_label"])
        stub_hooks.stops(topic_id, reply)

    stub_hooks.emit_turn = start_child
    with client.websocket_connect(chat_ws_url(room["id"], "user-1")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 start child"})
        _drain_until_done(ws)
    _wait_work_idle()
    stub_hooks.emit_turn = emit

    screens = _record_screens(stub_hooks)
    before = len(_card_blocks(client, room["id"], thread["id"]))
    r = _say_on_card(client, room["id"], thread["id"], "这条先别做了")
    assert r.status_code == 200, r.text
    _wait_work_idle()

    # 说的话落在人说话的地方。
    said = [b["content"] for b in _card_blocks(client, room["id"], thread["id"])]
    assert len(said) > before
    assert "这条先别做了" in said

    # 被叫醒的是房间，而且这条活一块屏幕都没起。
    assert thread["id"] not in screens, "为一条活起了屏幕——这是在复活容器"
    assert screens == [room["id"]], f"叫醒的不是房间：{screens}"
    prompt = stub_hooks.last_prompt or ""
    assert thread["id"] in prompt, "不说是哪条活，房间不知道该找哪个分身"
    assert "这条先别做了" in prompt, "人说的话没带过去"
    assert "native child=worker-1" in prompt
    assert stub_hooks.last_resume_session_id == "sess-test-1"

    async def assert_replaced_worker_is_fenced():
        from datetime import UTC, datetime

        import pytest

        from app.api.deps import get_chat_service
        from app.core.errors import ValidationError
        from app.domain.delivery.agent import (
            begin_send,
            dispatch_pending,
            record_task_instruction,
        )
        from app.domain.delivery.ledger import DeliveryEvent
        from app.domain.notification.models import NotificationType
        from app.domain.room_task.models import Task
        from app.main import app

        chat = app.dependency_overrides[get_chat_service]()
        task_id = uuid.UUID(thread["id"])
        async with client.test_factory() as session:
            task = await session.get(Task, task_id)
            original_turn = task.execution_turn_id
        # The original turn is over. A delayed native start cannot steal the card.
        await chat._note_worker(
            task_id,
            "late-child",
            topic_id=uuid.UUID(room["id"]),
            turn_id=original_turn,
            parent_session_id="late-parent",
        )
        async with client.test_factory() as session:
            task = await session.get(Task, task_id)
            assert task.subagent_id == "worker-1"
            assert task.execution_parent_session_id == "sess-test-1"
            await record_task_instruction(
                session,
                DeliveryEvent(
                    id=uuid.uuid4(),
                    type=NotificationType.ROOM_NOTICE,
                    payload={},
                    occurred_at=datetime.now(UTC),
                ),
                task=task,
                content="stop worker-1",
            )
            await session.commit()
        attempts = []

        class Recorder:
            def submit(self, *args, **kwargs):
                attempts.append(kwargs)

        await dispatch_pending(client.test_factory, chat=chat, runner=Recorder())
        assert len(attempts) == 1
        async with client.test_factory() as session:
            task = await session.get(Task, task_id)
            task.subagent_id = "replacement-child"
            await session.commit()
        attempt = attempts[0]
        with pytest.raises(ValidationError):
            await begin_send(
                client.test_factory,
                attempt["delivery_id"],
                attempt["turn_id"],
                parent_session_id="sess-test-1",
            )
        async with client.test_factory() as session:
            assert (
                await session.get(Delivery, attempt["delivery_id"])
            ).state == "failed"

    client.portal.call(assert_replaced_worker_is_fenced)


def test_a_card_has_no_chat_socket_of_its_own(client, stub_hooks):
    """对着活的 id 连聊天通道 —— 那不是一个地点，连不上。

    这不是一条被特意加上的拒绝：聊天通道认的是房间，活的 id 名下没有房间，所以
    它自然连不上。人要在卡下面说话，走的是那张卡的地址。
    """
    p = _project(client)
    room = _room(client, p["id"])
    thread = _thread(client, room["id"])
    _wait_work_idle()

    with client.websocket_connect(chat_ws_url(thread["id"], "user-1")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 进度怎么样"})
        frames = _drain_until_done(ws)

    assert frames[-1]["type"] == "error", frames


def test_a_room_still_answers_on_its_own_line(client, stub_hooks):
    """改的只是活那条路：房间自己被 @，照旧自己跑这一轮。"""
    p = _project(client)
    room = _room(client, p["id"])

    screens = _record_screens(stub_hooks)
    with client.websocket_connect(chat_ws_url(room["id"], "user-1")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 在吗"})
        _drain_until_done(ws)
    _wait_work_idle()

    assert screens == [room["id"]]
    authors = [b["author"] for b in _blocks(client, room["id"])]
    assert any(looks_like_agent_handle(a) for a in authors), "房间没答话"


def test_a_card_without_observed_parent_waits_without_waking_the_default(
    client, stub_hooks
):
    room = _room(client, _project(client)["id"])
    task = _thread(client, room["id"])
    screens = _record_screens(stub_hooks)
    response = _say_on_card(client, room["id"], task["id"], "stop this child")
    assert response.status_code == 200
    _wait_work_idle()
    assert screens == []

    async def check():
        async with client.test_factory() as session:
            row = await session.scalar(
                select(Delivery).where(Delivery.task_id == uuid.UUID(task["id"]))
            )
            assert row.state == "pending"
            assert row.agent_instance_id is None
            assert row.payload["content"].find("stop this child") >= 0

    asyncio.run(check())
