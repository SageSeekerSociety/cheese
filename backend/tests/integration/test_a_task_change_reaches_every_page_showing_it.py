"""A task is shown in two places at once: its own page, which listens on the
task's conversation, and its channel, where its card hangs under a message and
in the 支线 pane. Whatever changes what the task reads as reaches both without
a reload, whichever path made the change.
"""

import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.api.deps import get_chat_service
from app.domain.agent import runner_reports, session_state
from app.domain.agent.announce import announce
from app.domain.agent.reads import Report, SessionExited
from app.domain.agent_instance.adapters import task_teammate
from app.domain.delivery.agent import record_task_instruction
from app.domain.review.pr_poll import sweep_closings
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.presentation import Building, NeedsYou
from tests.ask_fixtures import active_ask
from tests.integration.conftest import (
    new_project,
    open_task,
    room_agent_seat,
    room_socket,
    session_auth_headers,
    task_agent_headers,
)

#: How many ping round trips to wait for a frame before calling it not sent.
#: A frame goes out right after the change commits; each round trip lets the
#: server finish what it was doing first.
_ROUND_TRIPS = 40


def _told(ws, room: str) -> bool:
    """Whether this page is told that the room's tasks changed."""
    for _ in range(_ROUND_TRIPS):
        ws.send_json({"type": "ping"})
        while True:
            frame = ws.receive_json()
            if frame["type"] == "state" and frame.get("id") == room:
                return True
            if frame["type"] == "pong":
                break
        time.sleep(0.05)
    return False


def _drain(ws) -> None:
    """Read what is already on its way, so a later `_told` sees only new frames."""
    ws.send_json({"type": "ping"})
    while ws.receive_json()["type"] != "pong":
        pass


def _channel_card(client, room: str, task: str) -> dict:
    """The task as its channel's 支线 pane reads it."""
    r = client.get(
        f"/topics/{room}/tasks",
        params={"limit": 0},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    (row,) = [t for t in r.json()["data"]["data"] if t["id"] == task]
    return row


def _task_read(client, task: str) -> dict:
    r = client.get(f"/topics/{task}/task", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_the_task_page_hears_the_task_close_once_its_summary_is_done(client):
    """「采纳并完成任务」: the task closes after its teammate writes it up. The
    task's own page is told then, so its input gives way to 「任务已关闭」 without
    a reload."""
    project = new_project(client, owner="alice")
    room = project["root_topic_id"]
    task = open_task(client, room, owner="alice")["id"]

    async def landed_long_ago() -> None:
        async with client.test_factory() as session:
            row = await session.get(Task, uuid.UUID(task))
            row.closing_since = datetime.now(UTC) - timedelta(hours=1)
            row.accepted_at = row.closing_since
            await session.commit()

    client.portal.call(landed_long_ago)
    chat = client.app.dependency_overrides[get_chat_service]()
    with (
        room_socket(client, task, "alice") as task_page,
        room_socket(client, room, "alice") as channel,
    ):
        _drain(task_page)
        _drain(channel)
        client.portal.call(
            lambda: sweep_closings(
                chat,
                announce,
                record_instruction=record_task_instruction,
                task_teammate=task_teammate,
            )
        )

        assert _told(task_page, room)
        assert _told(channel, room)
    assert _task_read(client, task)["status"] == TaskStatus.closed


def test_a_question_asked_in_a_task_is_heard_in_its_channel(
    client, stub_hooks, monkeypatch
):
    """芝士 asks in a task it just opened: the channel's card for it reads
    待回答 like the task does, not 讨论中 until someone reloads."""
    project = new_project(client, owner="alice")
    room = project["root_topic_id"]
    task = open_task(client, room, owner="alice")["id"]

    with room_socket(client, room, "alice") as channel:
        _drain(channel)
        with active_ask(
            client, stub_hooks, monkeypatch, task, platform_turn=True
        ) as headers:
            _drain(channel)
            r = client.post(
                f"/topics/{task}/asks",
                json={
                    "questions": [
                        {
                            "question": "预算按哪个口径统计",
                            "options": [{"text": "按部门"}, {"text": "按项目"}],
                        }
                    ]
                },
                headers=headers,
            )
            assert r.status_code == 200, r.text

            assert _told(channel, room)

    card = _channel_card(client, room, task)
    assert card["presentation"]["phrase"] == NeedsYou.awaiting_answer


def test_the_teammates_report_in_a_task_is_heard_in_its_channel(client):
    """The task's AI teammate says it is waiting on a build: the channel's card
    for the task reads 在等结果, like the task's own page, without either
    reloading."""
    project = new_project(client, owner="alice")
    room = project["root_topic_id"]
    task = open_task(client, room, owner="alice")["id"]
    seat = room_agent_seat(client, room)

    with (
        room_socket(client, task, "alice") as task_page,
        room_socket(client, room, "alice") as channel,
    ):
        _drain(task_page)
        _drain(channel)
        r = client.put(
            f"/topics/{task}/task/progress",
            json={"state": "waiting", "note": "等 CI 跑完"},
            headers=task_agent_headers(project["id"], task, seat),
        )
        assert r.status_code == 200, r.text

        assert _told(task_page, room)
        assert _told(channel, room)
    card = _channel_card(client, room, task)
    assert card["presentation"]["phrase"] == Building.waiting


def test_the_task_session_ending_is_heard_in_its_channel(client):
    """The seat's runner reports the task's session gone: both pages are told,
    and read 「已离开」 beside what the teammate last said."""
    project = new_project(client, owner="alice")
    room = project["root_topic_id"]
    task = open_task(client, room, owner="alice")["id"]
    seat = room_agent_seat(client, room)
    # 「已离开」 sits beside a report; with none on the task there is nothing to
    # mark, and so nothing for the pages to hear.
    said = client.put(
        f"/topics/{task}/task/progress",
        json={"state": "waiting", "note": "等构建"},
        headers=task_agent_headers(project["id"], task, seat),
    )
    assert said.status_code == 200, said.text
    key = session_state.SeatKey(uuid.UUID(task), seat, "claude-code")
    epoch = client.portal.call(session_state.register, client.test_factory, key)
    chat = SimpleNamespace(
        session_factory=client.test_factory, live=SimpleNamespace(unread_inputs={})
    )

    with (
        room_socket(client, task, "alice") as task_page,
        room_socket(client, room, "alice") as channel,
    ):
        _drain(task_page)
        _drain(channel)
        exited = Report(
            key.conversation_id,
            seat,
            key.harness,
            epoch,
            epoch,
            1,
            datetime.now(UTC),
            SessionExited("launch-1"),
        )
        assert client.portal.call(runner_reports.apply, chat, exited) is not False

        assert _told(task_page, room)
        assert _told(channel, room)
    assert _task_read(client, task)["agent_left_at"] is not None
