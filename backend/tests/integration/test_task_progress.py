"""A task shows what its AI teammate last said about its work.

The teammate says it through its own session (`cheese_task_state`); the board,
the task's page and the project's lists all show that, and nothing the
platform sees of the session rewrites it: a turn ending does not, and neither
does the session going away, which only puts 「已离开」 beside it. While its
session runs and the report has stopped moving, the platform asks for a new
one.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.core.config import settings
from app.domain.room_task.models import Task
from app.domain.room_task.progress import note_agent_left
from tests.integration.conftest import (
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
    task_agent_headers,
)


def _a_task(client) -> tuple[str, str, str]:
    project = post_project(client, {"name": "Progress"}, owner="alice").json()["data"]
    room = project["root_topic_id"]
    seat = room_agent_seat(client, room)
    task = open_task(client, room, "跑一遍迁移", owner="alice")
    return project["id"], task["id"], seat


def _report(client, project_id, task_id, seat, state, note=None):
    body = {"state": state, **({"note": note} if note is not None else {})}
    return client.put(
        f"/topics/{task_id}/task/progress",
        json=body,
        headers=task_agent_headers(project_id, task_id, seat),
    )


def _task(client, task_id: str) -> dict:
    r = client.get(f"/topics/{task_id}/task", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_a_task_whose_teammate_is_waiting_shows_waiting_with_no_turn_running(client):
    """Waiting on a build is still being on the work: with no turn open in the
    task, the board says what the teammate said, not 「进行中」."""
    project_id, task_id, seat = _a_task(client)

    said = _report(client, project_id, task_id, seat, "waiting", "等 CI 跑完")
    assert said.status_code == 200, said.text

    task = _task(client, task_id)
    assert task["presentation"] == {"column": "building", "phrase": "waiting"}
    assert task["progress_note"] == "等 CI 跑完"
    assert task["progress_set_by"] == seat
    listed = client.get(
        f"/projects/{project_id}/tasks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    row = next(r for r in listed if r["id"] == task_id)
    assert row["presentation"]["phrase"] == "waiting"


def test_a_blocked_task_waits_on_its_owner(client):
    """「卡住了」 puts the next step in the owner's hands: it is on her list."""
    project_id, task_id, seat = _a_task(client)

    assert _report(client, project_id, task_id, seat, "blocked").status_code == 200

    assert _task(client, task_id)["presentation"] == {
        "column": "needs_you",
        "phrase": "blocked",
    }
    mine = client.get("/awaiting-me", headers=session_auth_headers("alice"))
    assert mine.status_code == 200, mine.text
    assert any(item.get("taskId") == task_id for item in mine.json()["data"]["data"])


def test_only_the_tasks_own_session_reports_its_progress(client):
    """A person reads the report; they do not write it for the teammate."""
    _project_id, task_id, _seat = _a_task(client)

    refused = client.put(
        f"/topics/{task_id}/task/progress",
        json={"state": "working"},
        headers=session_auth_headers("alice"),
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "taskOwnSessionReportsProgress"
    assert _task(client, task_id)["progress_state"] is None


def test_the_session_leaving_does_not_rewrite_what_the_teammate_said(client):
    """The session ending is noted beside the report, never over it; reporting
    again takes the note away."""
    project_id, task_id, seat = _a_task(client)
    assert (
        _report(client, project_id, task_id, seat, "waiting", "等构建").status_code
        == 200
    )

    async def leave() -> bool:
        async with client.test_factory() as session:
            marked = await note_agent_left(
                session, uuid.UUID(task_id), at=datetime.now(UTC)
            )
            await session.commit()
            return marked

    assert client.portal.call(leave) is True

    task = _task(client, task_id)
    assert task["progress_state"] == "waiting"
    assert task["progress_note"] == "等构建"
    assert task["presentation"]["phrase"] == "waiting"
    assert task["agent_left_at"] is not None

    assert _report(client, project_id, task_id, seat, "working").status_code == 200
    assert _task(client, task_id)["agent_left_at"] is None


class _Chat:
    """What the reminder needs of the chat service: a database and a way into
    the running session, which here only records what it was told."""

    def __init__(self, client) -> None:
        self.session_factory = client.test_factory
        self.told: list[tuple[uuid.UUID, str]] = []

    async def notify_running_turn(self, topic_id: uuid.UUID, notice: str) -> bool:
        self.told.append((topic_id, notice))
        return True


def _said_at(client, task_id: str, at: datetime | None) -> None:
    async def stamp() -> None:
        async with client.test_factory() as session:
            await session.execute(
                update(Task)
                .where(Task.id == uuid.UUID(task_id))
                .values(progress_state="working", progress_set_at=at)
            )
            await session.commit()

    client.portal.call(stamp)


def test_a_running_session_whose_report_went_stale_is_asked_for_a_new_one(client):
    from app.domain.agent.turn.steps.reminders import StaleProgressReminder

    _project_id, task_id, _seat = _a_task(client)
    threshold = timedelta(seconds=settings.chat_progress_reminder_after_s)
    now = datetime.now(UTC)
    chat = _Chat(client)
    running: set[uuid.UUID] = {uuid.UUID(task_id)}

    async def sessions_running(_session):
        return running

    remind = StaleProgressReminder(chat, sessions_running)

    _said_at(client, task_id, now - threshold / 2)
    assert client.portal.call(remind, now) == 0

    _said_at(client, task_id, now - threshold - timedelta(minutes=1))
    assert client.portal.call(remind, now) == 1
    assert chat.told[0][0] == uuid.UUID(task_id)
    assert "cheese_task_state" in chat.told[0][1]
    # Asked once; the next ask waits another threshold from this one.
    assert client.portal.call(remind, now + threshold / 2) == 0
    assert client.portal.call(remind, now + threshold) == 1

    # A session that is not running is not asked, however old the report.
    running.clear()
    assert client.portal.call(remind, now + 3 * threshold) == 0


def test_a_teammate_that_never_reported_is_asked_once_its_session_has_run_a_while(
    client,
):
    from app.domain.agent.turn.steps.reminders import StaleProgressReminder

    _project_id, task_id, _seat = _a_task(client)
    threshold = timedelta(seconds=settings.chat_progress_reminder_after_s)
    now = datetime.now(UTC)
    chat = _Chat(client)

    async def sessions_running(_session):
        return {uuid.UUID(task_id)}

    remind = StaleProgressReminder(chat, sessions_running)

    assert client.portal.call(remind, now) == 0
    assert client.portal.call(remind, now + threshold) == 1
    assert "cheese_task_state" in chat.told[0][1]
