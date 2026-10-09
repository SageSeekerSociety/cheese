"""A task is shown in two places at once: its own page, which listens on the
task's conversation, and its channel, where its card hangs under a message and
in the 支线 pane. Whatever changes what the task reads as reaches both without
a reload, whichever path made the change.
"""

import time
import uuid
from datetime import UTC, datetime, timedelta

from app.api.deps import get_chat_service
from app.domain.review.landing_watch import watch_landings
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.presentation import NeedsYou
from tests.ask_fixtures import active_ask
from tests.integration.conftest import (
    new_project,
    open_task,
    room_socket,
    session_auth_headers,
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
        client.portal.call(lambda: watch_landings(chat))

        assert _told(task_page, room)
        assert _told(channel, room)
    assert _task_read(client, task)["status"] == TaskStatus.closed


def test_starting_a_task_is_heard_on_its_page_and_in_its_channel(client):
    project = new_project(client, owner="alice")
    room = project["root_topic_id"]
    task = open_task(client, room, owner="alice", start=False)["id"]

    with (
        room_socket(client, task, "alice") as task_page,
        room_socket(client, room, "alice") as channel,
    ):
        _drain(task_page)
        _drain(channel)
        r = client.post(
            f"/topics/{task}/start",
            json={"reviewer_handle": "alice"},
            headers=session_auth_headers("alice"),
        )
        assert r.status_code == 200, r.text

        assert _told(task_page, room)
        assert _told(channel, room)


def test_a_question_asked_in_a_task_is_heard_in_its_channel(
    client, stub_hooks, monkeypatch
):
    """芝士 asks in a task it just opened: the channel's card for it reads
    待回答 like the task does, not 讨论中 until someone reloads."""
    project = new_project(client, owner="alice")
    room = project["root_topic_id"]
    task = open_task(client, room, owner="alice", start=False)["id"]

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

    r = client.get(
        f"/topics/{room}/tasks",
        params={"limit": 0},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    (row,) = [t for t in r.json()["data"]["data"] if t["id"] == task]
    assert row["presentation"]["phrase"] == NeedsYou.awaiting_answer
