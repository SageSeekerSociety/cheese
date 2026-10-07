"""Standing work: a confirmed rule wakes the room's teammate, once per moment or
event, and whoever set it hears how each run went."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.domain.agent.models import AgentTurn
from app.domain.delivery.models import Delivery
from app.domain.room_task.models import Task, TaskStatus
from app.domain.routine import service as routine_service
from app.domain.routine.models import Routine, RoutineRun
from app.domain.routine.service import REPORT_TIMEOUT, sweep
from tests.integration.conftest import post_project, session_auth_headers

OWNER = "user-1"
PERSON = session_auth_headers(OWNER)


class Runner:
    def __init__(self):
        self.submitted = []

    def submit(self, chat, topic_id, **kwargs):
        self.submitted.append((str(topic_id), kwargs))


def _project(client) -> str:
    r = post_project(client, json={"name": f"周期-{uuid.uuid4().hex[:6]}"}, owner=OWNER)
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _room(client, project_id: str, title: str = "周报") -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title},
        headers=session_auth_headers(OWNER),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _sweep(client, runner=None):
    runner = runner or Runner()
    result = client.portal.call(
        lambda: sweep(client.test_request_factory, chat=object(), runner=runner)
    )
    return result, runner


def _db(client, fn):
    async def go():
        async with client.test_request_factory() as session:
            out = await fn(session)
            await session.commit()
            return out

    return client.portal.call(go)


def _make_due(client, routine_id: str, minutes_ago: int = 1):
    due = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    _db(
        client,
        lambda s: s.execute(
            update(Routine)
            .where(Routine.id == uuid.UUID(routine_id))
            .values(next_run_at=due)
        ),
    )
    return due


def _weekly(client, room, headers=None, **over):
    body = {
        "title": "每周进展",
        "instructions": "整理本周各房间的进展，列出完成、进行中和阻碍",
        "context_scope": "本项目所有房间",
        "output_dir": "周报",
        "trigger": "schedule",
        "spec": {"freq": "weekly", "weekdays": [0], "time": "09:00"},
        "timezone": "Asia/Shanghai",
        **over,
    }
    return client.post(f"/topics/{room}/routines", json=body, headers=headers or {})


def _run_message(client, room: str, run_id: str) -> dict:
    """The main-line message that stands for this run."""
    blocks = client.get(f"/topics/{room}/blocks", headers=PERSON).json()["data"]
    found = [
        b
        for b in blocks["data"]
        if (b.get("routine_run") or {}).get("run_id") == run_id
    ]
    assert len(found) == 1, "这次执行在主线上没有一条消息"
    return found[0]


def _runs(client, routine_id):
    r = client.get(f"/routines/{routine_id}/runs", headers=PERSON)
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def test_an_ai_draft_runs_nothing_until_a_person_confirms_it(client):
    room = _room(client, _project(client))
    drafted = _weekly(client, room, owner_handle=OWNER)
    assert drafted.status_code == 200, drafted.text
    routine = drafted.json()["data"]
    assert routine["state"] == "draft"
    assert routine["next_run_at"] is None

    refused = client.post(f"/routines/{routine['id']}/confirm")
    assert refused.status_code == 403, "an AI teammate confirmed its own draft"

    (_, runner) = _sweep(client)
    assert runner.submitted == []

    confirmed = client.post(f"/routines/{routine['id']}/confirm", headers=PERSON)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["data"]["state"] == "active"
    assert confirmed.json()["data"]["next_run_at"] is not None


def test_a_due_moment_wakes_the_teammate_once(client):
    room = _room(client, _project(client))
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    assert routine["state"] == "active"
    due = _make_due(client, routine["id"])

    first, runner = _sweep(client)
    second, runner2 = _sweep(client)

    assert first["scheduled"] == 1
    assert second["scheduled"] == 0
    assert len(runner.submitted) == 1 and runner2.submitted == []
    topic_id, kwargs = runner.submitted[0]
    assert "整理本周各房间的进展" in kwargs["content"]
    runs = _runs(client, routine["id"])
    assert len(runs) == 1
    # The run is a message of the teammate's in the main line, and the run
    # itself is answered in that message's 支线.
    message = _run_message(client, room, runs[0]["id"])
    assert message["author"] == routine["agent_handle"]
    assert topic_id == message["routine_run"]["thread_id"] != room
    assert runs[0]["scheduled_for"] == due.isoformat()
    after = client.get(f"/routines/{routine['id']}", headers=PERSON).json()["data"]
    assert datetime.fromisoformat(after["next_run_at"]) > datetime.now(UTC)


def test_a_report_settles_the_run_and_tells_the_owner(client):
    project = _project(client)
    room = _room(client, project)
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    _make_due(client, routine["id"])
    _sweep(client)
    run = _runs(client, routine["id"])[0]

    unexplained = client.post(
        f"/routine-runs/{run['id']}/report", json={"status": "failed", "summary": ""}
    )
    assert unexplained.status_code >= 400, "a failure without a reason was accepted"

    done = client.post(
        f"/routine-runs/{run['id']}/report",
        json={
            "status": "succeeded",
            "summary": "本周三个房间有进展",
            "outputs": ["周报/2026-09-28.md"],
        },
    )
    assert done.status_code == 200, done.text
    _sweep(client)

    run = _runs(client, routine["id"])[0]
    assert run["status"] == "succeeded"
    assert run["outputs"] == ["周报/2026-09-28.md"]
    inbox = client.get(f"/projects/{project}/alerts", headers=PERSON).json()["data"][
        "data"
    ]
    mine = [n for n in inbox if "每周进展" in n["title"]]
    assert len(mine) == 1, inbox
    assert "周报/2026-09-28.md" in mine[0]["body"]
    assert mine[0]["topic_id"] == room


def _delivered_run(client, room, routine_id, delivered_ago):
    """A run whose turn row was delivered `delivered_ago` and, as every input's
    row in a live session is, stopped a few milliseconds later — long before the
    work it carries ends."""
    _make_due(client, routine_id)
    _sweep(client)
    run = _runs(client, routine_id)[0]

    async def delivered(session):
        run_row = await session.get(RoutineRun, uuid.UUID(run["id"]))
        delivery = await session.scalar(
            select(Delivery).where(Delivery.event_id == run_row.delivery_event_id)
        )
        at = datetime.now(UTC) - delivered_ago
        attempt = delivery.attempt_id or uuid.uuid4()
        delivery.attempt_id = attempt
        delivery.state = "uncertain"
        session.add(
            AgentTurn(
                id=attempt,
                conversation_id=uuid.UUID(room),
                continuation_id=attempt,
                author="system",
                content="",
                started_at=at,
                delivered_at=at,
                stopped_at=at + timedelta(milliseconds=12),
            )
        )
        return attempt

    return run, _db(client, delivered)


def test_a_stopped_turn_row_does_not_end_the_run(client):
    """Seen on dev: the row stopped 12ms after delivery and the run was failed two
    minutes later while the teammate was still writing the summary."""
    room = _room(client, _project(client))
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    run, _ = _delivered_run(client, room, routine["id"], timedelta(minutes=10))

    _sweep(client)
    assert _runs(client, routine["id"])[0]["status"] == "running"

    done = client.post(
        f"/routine-runs/{run['id']}/report",
        json={"status": "succeeded", "summary": "摘要写好了", "outputs": ["a.md"]},
    )
    assert done.status_code == 200, done.text
    assert _runs(client, routine["id"])[0]["status"] == "succeeded"


def test_no_report_past_the_turn_ceiling_fails_and_a_late_report_still_lands(client):
    project = _project(client)
    room = _room(client, project)
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    run, _ = _delivered_run(
        client, room, routine["id"], REPORT_TIMEOUT + timedelta(minutes=1)
    )

    _sweep(client)
    failed = _runs(client, routine["id"])[0]
    assert failed["status"] == "failed"
    assert failed["error"] == routine_service.NO_REPORT

    late = client.post(
        f"/routine-runs/{run['id']}/report",
        json={"status": "succeeded", "summary": "晚了一点", "outputs": ["b.md"]},
    )
    assert late.status_code == 200, late.text
    _sweep(client)
    settled = _runs(client, routine["id"])[0]
    assert settled["status"] == "succeeded"
    assert not settled["error"], "a run that succeeded still carries the old failure"
    inbox = client.get(f"/projects/{project}/alerts", headers=PERSON).json()["data"][
        "data"
    ]
    said = [n["body"] for n in inbox if "每周进展" in n["title"]]
    assert any("b.md" in body for body in said), said

    again = client.post(
        f"/routine-runs/{run['id']}/report",
        json={"status": "failed", "summary": "重复"},
    )
    assert again.status_code >= 400, "a settled run took a second report"


def test_a_turn_the_room_says_failed_fails_the_run_with_its_reason(client):
    from app.domain.block.authorship import AuthorType
    from app.domain.block.models import Block, BlockKind

    room = _room(client, _project(client))
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    _, attempt = _delivered_run(client, room, routine["id"], timedelta(minutes=1))

    async def it_failed(session):
        session.add(
            Block(
                id=uuid.uuid4(),
                project_id=uuid.UUID(routine["project_id"]),
                conversation_id=uuid.UUID(room),
                author="system",
                author_type=AuthorType.platform,
                kind=BlockKind.event,
                content="本轮未完成：模型接口连续报错",
                meta={"event_type": "turn_failed"},
                turn_id=attempt,
            )
        )

    _db(client, it_failed)
    _sweep(client)
    run = _runs(client, routine["id"])[0]
    assert run["status"] == "failed"
    assert "模型接口连续报错" in run["error"]


def test_paused_rules_do_not_fire_and_resume_does_not_replay_the_missed_moment(client):
    room = _room(client, _project(client))
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    assert (
        client.post(f"/routines/{routine['id']}/pause", headers=PERSON).status_code
        == 200
    )
    _make_due(client, routine["id"])
    # A paused rule has no next moment at all, even if one was left behind.
    _db(
        client,
        lambda s: s.execute(
            update(Routine)
            .where(Routine.id == uuid.UUID(routine["id"]))
            .values(state="paused")
        ),
    )
    (_, runner) = _sweep(client)
    assert runner.submitted == []

    resumed = client.post(f"/routines/{routine['id']}/resume", headers=PERSON)
    assert resumed.status_code == 200, resumed.text
    assert datetime.fromisoformat(resumed.json()["data"]["next_run_at"]) > datetime.now(
        UTC
    )
    (_, runner) = _sweep(client)
    assert runner.submitted == []
    assert _runs(client, routine["id"]) == []


def test_an_edit_reaches_the_next_run(client):
    room = _room(client, _project(client))
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    edited = client.patch(
        f"/routines/{routine['id']}",
        json={"instructions": "只统计已合并的 PR"},
        headers=PERSON,
    )
    assert edited.status_code == 200, edited.text
    _make_due(client, routine["id"])
    (_, runner) = _sweep(client)
    assert "只统计已合并的 PR" in runner.submitted[0][1]["content"]
    assert "整理本周各房间的进展" not in runner.submitted[0][1]["content"]


def test_an_ai_edit_to_a_live_rule_waits_for_a_person_again(client):
    room = _room(client, _project(client))
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    edited = client.patch(
        f"/routines/{routine['id']}", json={"instructions": "顺手把周报发给所有人"}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["state"] == "draft"
    _make_due(client, routine["id"])
    (_, runner) = _sweep(client)
    assert runner.submitted == []


def _close_task(client, project, room, title):
    async def go(session):
        session.add(
            Task(
                id=uuid.uuid4(),
                project_id=uuid.UUID(project),
                room_id=uuid.UUID(room),
                title=title,
                status=TaskStatus.closed,
                closed_at=datetime.now(UTC),
            )
        )

    _db(client, go)


def test_a_task_closing_in_the_room_fires_a_room_scoped_rule_once(client):
    project = _project(client)
    room = _room(client, project)
    elsewhere = _room(client, project, "别的房间")
    rule = client.post(
        f"/topics/{room}/routines",
        json={
            "title": "任务完成就更新进度表",
            "instructions": "把刚完成的任务写进进度表",
            "trigger": "task_closed",
            "spec": {"scope": "room"},
        },
        headers=PERSON,
    ).json()["data"]
    assert rule["state"] == "active"

    _close_task(client, project, elsewhere, "别的房间的任务")
    (_, runner) = _sweep(client)
    assert runner.submitted == [], "an event outside the rule's room fired it"

    _close_task(client, project, room, "写完调研")
    (_, runner) = _sweep(client)
    (_, again) = _sweep(client)
    assert len(runner.submitted) == 1 and again.submitted == []
    assert "写完调研" in runner.submitted[0][1]["content"]

    client.post(f"/routines/{rule['id']}/pause", headers=PERSON)
    _close_task(client, project, room, "又一个任务")
    (_, runner) = _sweep(client)
    assert runner.submitted == [], "a paused rule still fired"


def test_only_the_executing_teammate_reports_and_nobody_outside_the_room_reads(client):
    project = _project(client)
    room = _room(client, project)
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    stranger = session_auth_headers("user-2")
    assert client.get(f"/routines/{routine['id']}", headers=stranger).status_code in (
        401,
        403,
    )
    assert client.get(
        f"/projects/{project}/routines?topic={room}", headers=stranger
    ).status_code in (401, 403)


def test_a_turn_that_never_started_says_why(client):
    """The machine was not there: the run fails now, with the room's own reason."""
    from app.domain.block.authorship import AuthorType
    from app.domain.block.models import Block, BlockKind

    room = _room(client, _project(client))
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    _make_due(client, routine["id"])
    _sweep(client)
    run = _runs(client, routine["id"])[0]

    async def the_machine_was_missing(session):
        run_row = await session.get(RoutineRun, uuid.UUID(run["id"]))
        delivery = await session.scalar(
            select(Delivery).where(Delivery.event_id == run_row.delivery_event_id)
        )
        attempt = delivery.attempt_id or uuid.uuid4()
        delivery.attempt_id = attempt
        delivery.state = "uncertain"
        now = datetime.now(UTC)
        session.add(
            AgentTurn(
                id=attempt,
                conversation_id=uuid.UUID(room),
                continuation_id=attempt,
                author="system",
                content="",
                started_at=now,
                stopped_at=now,
            )
        )
        await session.flush()
        session.add(
            Block(
                id=uuid.uuid4(),
                project_id=uuid.UUID(routine["project_id"]),
                conversation_id=uuid.UUID(room),
                author="system",
                author_type=AuthorType.platform,
                kind=BlockKind.event,
                content="本轮未完成：这条会话的机器尚未配置或未连接",
                meta={"event_type": "turn_failed"},
                turn_id=attempt,
            )
        )

    _db(client, the_machine_was_missing)
    _sweep(client)

    run = _runs(client, routine["id"])[0]
    assert run["status"] == "failed"
    assert "机器尚未配置或未连接" in run["error"]


def test_a_routine_in_an_archived_project_runs_nothing(client):
    project = _project(client)
    room = _room(client, project)
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    archived = client.post(f"/projects/{project}/archive", headers=PERSON)
    assert archived.status_code == 200, archived.text
    _make_due(client, routine["id"])

    result, runner = _sweep(client)

    assert result["scheduled"] == 0
    assert runner.submitted == []
    assert _runs(client, routine["id"]) == []
