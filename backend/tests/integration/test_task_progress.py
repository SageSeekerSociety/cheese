"""A task shows what its AI teammate last said about its work.

The teammate says it through its own session (`cheese_task(action=state)`); the board,
the task's page and the project's lists all show that, and the session
stopping does not rewrite it: a turn ending does not, and neither does the
session going away, which only puts 「已离开」 beside it. (A session running
after the report is newer than it: `test_a_task_hears_its_session_through_the_runner`.) While its
session runs and the report has stopped moving, the platform asks for a new
one.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.core.config import settings
from app.domain.review.models import AcceptCard
from app.domain.review.notes import NoteCode
from app.domain.room_task.models import Task
from app.domain.room_task.progress import note_agent_left
from tests.delivery import delivery_artifact
from tests.integration.conftest import (
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
    task_agent_headers,
)
from tests.machine_work import declare_task, machine_commits


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


def test_a_blocked_row_on_the_owners_list_says_what_the_teammate_needs(client):
    """The owner's list says what the teammate is stuck on: the note it gave
    with its 「卡住了」 is the row's second line. A report with no note leaves the
    line for the screen to fill, not a stale sentence from somewhere else."""
    project_id, task_id, seat = _a_task(client)

    def row() -> dict:
        mine = client.get("/awaiting-me", headers=session_auth_headers("alice"))
        assert mine.status_code == 200, mine.text
        return next(
            i for i in mine.json()["data"]["data"] if i.get("taskId") == task_id
        )

    assert (
        _report(
            client, project_id, task_id, seat, "blocked", "需要仓库的写权限才能推送"
        ).status_code
        == 200
    )
    assert row()["detail"] == "需要仓库的写权限才能推送"

    assert _report(client, project_id, task_id, seat, "blocked").status_code == 200
    assert row()["detail"] == ""


def test_a_blocked_row_names_the_teammate_by_the_name_the_project_gave_it(client):
    """When the teammate left no note, the owner's list says who is stuck: a
    teammate renamed in this project is called by its new name, not the
    default one."""
    project_id, task_id, seat = _a_task(client)
    agents = client.get(
        f"/projects/{project_id}/agents", headers=session_auth_headers("alice")
    ).json()["data"]
    agents = agents.get("data", agents) if isinstance(agents, dict) else agents
    for agent in agents:
        r = client.put(
            f"/projects/{project_id}/agents/{agent['id']}",
            json={"display_name": "小研"},
            headers=session_auth_headers("alice"),
        )
        assert r.status_code == 200, r.text

    assert _report(client, project_id, task_id, seat, "blocked").status_code == 200

    mine = client.get("/awaiting-me", headers=session_auth_headers("alice"))
    row = next(i for i in mine.json()["data"]["data"] if i.get("taskId") == task_id)
    assert row["agentName"] == "小研"
    assert row["agentNameSource"] == "human"


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
                session, uuid.UUID(task_id), by=seat, at=datetime.now(UTC)
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

    async def notify_running_session(self, topic_id: uuid.UUID, notice: str) -> bool:
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
    assert "cheese_task(action=state)" in chat.told[0][1]
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
    assert "cheese_task(action=state)" in chat.told[0][1]


# —— one task, one state: a card's facts beat what the teammate said ——————————


def _file_card(client, project_id: str, task_id: str) -> dict:
    """The task's work handed over for review, as its teammate files it."""
    declare_task(uuid.UUID(project_id), uuid.UUID(task_id))
    machine_commits(
        uuid.UUID(project_id), uuid.UUID(task_id), {"login.txt": "fixed the login"}
    )
    room = _task(client, task_id)["room_id"]
    filed = client.post(
        f"/topics/{task_id}/accept-card",
        json={
            "change_subject": "修好了登录页",
            "focus": "最懂",
            **delivery_artifact(client, room),
        },
        headers=session_auth_headers("alice"),
    )
    assert filed.status_code == 200, filed.text
    return filed.json()["data"]


def test_a_card_filed_after_the_teammate_said_working_puts_the_task_in_review(
    client,
):
    """Filing a card is the fact the board follows: the task waits on its
    reviewer, who has it to act on, whatever the teammate said before."""
    project_id, task_id, seat = _a_task(client)
    assert _report(client, project_id, task_id, seat, "working").status_code == 200

    _file_card(client, project_id, task_id)

    task = _task(client, task_id)
    assert task["presentation"] == {"column": "needs_you", "phrase": "awaiting_review"}
    assert task["progress_state"] is None
    mine = client.get("/awaiting-me", headers=session_auth_headers("alice"))
    assert mine.status_code == 200, mine.text
    assert any(item.get("taskId") == task_id for item in mine.json()["data"]["data"])


def test_a_report_while_a_card_waits_on_review_is_refused(client):
    """While a card waits, the card says where the task stands: the teammate
    saying it is working is refused, and tells it why, and nothing moves."""
    project_id, task_id, seat = _a_task(client)
    _file_card(client, project_id, task_id)

    refused = _report(client, project_id, task_id, seat, "working", "接着改")

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "taskProgressWhileInReview"
    task = _task(client, task_id)
    assert task["presentation"]["phrase"] == "awaiting_review"
    assert task["progress_state"] is None


def test_the_teammate_cannot_say_its_task_awaits_review(client):
    """「待审阅」 is a card's fact, not something the teammate reports."""
    project_id, task_id, seat = _a_task(client)

    said = _report(client, project_id, task_id, seat, "review")

    assert said.status_code == 400, said.text
    assert _task(client, task_id)["progress_state"] is None


def test_filing_a_card_takes_the_left_mark_away_with_the_report(client):
    """「已离开」 was written beside a report; filing a card ends that report, so
    the mark goes with it rather than sitting on the task through 等审."""
    project_id, task_id, seat = _a_task(client)
    assert (
        _report(client, project_id, task_id, seat, "waiting", "等构建").status_code
        == 200
    )

    async def leave() -> bool:
        async with client.test_factory() as session:
            marked = await note_agent_left(
                session, uuid.UUID(task_id), by=seat, at=datetime.now(UTC)
            )
            await session.commit()
            return marked

    assert client.portal.call(leave) is True
    _file_card(client, project_id, task_id)

    assert _task(client, task_id)["agent_left_at"] is None


def test_a_returned_card_puts_the_task_back_to_work(client):
    """The reviewer returns the card: the task is back in its teammate's hands,
    and its teammate may report again."""
    project_id, task_id, seat = _a_task(client)
    card = _file_card(client, project_id, task_id)

    returned = client.post(
        f"/accept-cards/{card['id']}/reject",
        json={"decided_by": "alice", "note": "登录后跳转不对"},
        headers=session_auth_headers("alice"),
    )
    assert returned.status_code == 200, returned.text

    task = _task(client, task_id)
    assert task["progress_state"] == "working"
    assert task["presentation"]["phrase"] == "running"
    # Nobody reported it: the reviewer did not say the task is being worked on.
    assert task["progress_set_by"] is None
    assert _report(client, project_id, task_id, seat, "waiting").status_code == 200


def test_a_reopened_task_starts_from_open(client):
    """Reopened, a task is 开启 again: what its teammate said about the work
    that closed is not what it is doing now."""
    project_id, task_id, seat = _a_task(client)
    assert (
        _report(client, project_id, task_id, seat, "working", "改好了").status_code
        == 200
    )
    _close(client, task_id)

    reopened = client.post(
        f"/topics/{task_id}/reopen", headers=session_auth_headers("alice")
    )
    assert reopened.status_code == 200, reopened.text

    task = _task(client, task_id)
    assert (task["progress_state"], task["progress_note"]) == (None, None)
    assert task["presentation"]["phrase"] == "started"


def _close(client, task_id: str) -> None:
    closed = client.post(
        f"/topics/{task_id}/close", json={}, headers=session_auth_headers("alice")
    )
    assert closed.status_code == 200, closed.text


def test_another_tasks_session_cannot_report_this_tasks_progress(client):
    """A session reports the task it works on, not one next to it."""
    project_id, task_id, seat = _a_task(client)
    room = _task(client, task_id)["room_id"]
    other = open_task(client, room, "另一件事", owner="alice")["id"]

    refused = client.put(
        f"/topics/{task_id}/task/progress",
        json={"state": "blocked"},
        headers=task_agent_headers(project_id, other, seat),
    )

    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "tokenOtherTopic"
    assert _task(client, task_id)["progress_state"] is None


def test_a_closed_task_takes_no_progress_report(client):
    """A closed task says how it ended; its teammate does not reopen it by
    saying it is working."""
    project_id, task_id, seat = _a_task(client)
    _close(client, task_id)

    refused = _report(client, project_id, task_id, seat, "working")

    assert refused.status_code != 200, refused.text
    task = _task(client, task_id)
    assert task["status"] == "closed" and task["progress_state"] is None


def test_a_session_leaving_a_closed_task_marks_nothing(client):
    """「已离开」 is about an open task's work; a closed one says how it ended."""
    _project_id, task_id, seat = _a_task(client)
    _close(client, task_id)

    async def leave() -> bool:
        async with client.test_factory() as session:
            marked = await note_agent_left(
                session, uuid.UUID(task_id), by=seat, at=datetime.now(UTC)
            )
            await session.commit()
            return marked

    assert client.portal.call(leave) is False
    assert _task(client, task_id)["agent_left_at"] is None


def test_a_closed_task_is_not_asked_for_its_state(client):
    """A session still running in a task someone closed is not asked where the
    work stands: there is no work left to stand anywhere."""
    from app.domain.agent.turn.steps.reminders import StaleProgressReminder

    _project_id, task_id, _seat = _a_task(client)
    _said_at(client, task_id, datetime.now(UTC) - timedelta(days=1))
    _close(client, task_id)
    chat = _Chat(client)

    async def sessions_running(_session):
        return {uuid.UUID(task_id)}

    remind = StaleProgressReminder(chat, sessions_running)
    now = datetime.now(UTC)
    threshold = timedelta(seconds=settings.chat_progress_reminder_after_s)

    assert client.portal.call(remind, now) == 0
    assert client.portal.call(remind, now + 2 * threshold) == 0
    assert chat.told == []


@pytest.mark.parametrize(
    "card_state",
    [
        {},
        {"status": "pending", "note_code": NoteCode.checks_failed, "note": "CI 未通过"},
        {
            "status": "conflict",
            "note_code": NoteCode.merge_conflict,
            "note": "合并冲突",
        },
    ],
    ids=["awaiting_review", "fixing_checks", "resolving_conflict"],
)
def test_a_running_session_whose_task_has_an_open_card_is_not_asked(client, card_state):
    """While a card is open it says where the task stands (等审, fixing its
    checks, resolving a conflict), and a report would be refused: the platform
    does not ask the teammate for one, however long its session runs."""
    from app.domain.agent.turn.steps.reminders import StaleProgressReminder

    project_id, task_id, _seat = _a_task(client)
    card = _file_card(client, project_id, task_id)
    if card_state:

        async def seed() -> None:
            async with client.test_factory() as session:
                await session.execute(
                    update(AcceptCard)
                    .where(AcceptCard.id == uuid.UUID(card["id"]))
                    .values(**card_state)
                )
                await session.commit()

        client.portal.call(seed)
    threshold = timedelta(seconds=settings.chat_progress_reminder_after_s)
    now = datetime.now(UTC)
    chat = _Chat(client)

    async def sessions_running(_session):
        return {uuid.UUID(task_id)}

    remind = StaleProgressReminder(chat, sessions_running)

    asked = [client.portal.call(remind, now + k * threshold) for k in range(4)]

    assert asked == [0, 0, 0, 0]
    assert chat.told == []
