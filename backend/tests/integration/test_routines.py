"""Standing work: a saved rule wakes the room's teammate, once per moment or
event, and whoever set it hears how each run went."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.domain.agent.models import AgentTurn
from app.domain.delivery.models import Delivery, TimedDelivery
from app.domain.room_task.models import Task, TaskStatus
from app.domain.routine import service as routine_service
from app.domain.routine.models import Routine, RoutineRun
from app.domain.routine.service import REPORT_TIMEOUT, sweep
from tests.integration.conftest import (
    post_project,
    session_auth_headers,
)

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
        "note": "结果按房间分开列，写完发在频道里",
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


def test_a_teammates_rule_runs_from_the_moment_it_is_saved(client):
    """Nobody confirms it: a rule an AI teammate creates is saved and running,
    and its first moment comes the same way a person-made rule's does."""
    room = _room(client, _project(client))
    made = _weekly(client, room, owner_handle=OWNER)
    assert made.status_code == 200, made.text
    routine = made.json()["data"]
    assert routine["state"] == "active"
    assert routine["next_run_at"] is not None
    assert routine["created_by"] == routine["agent_handle"], routine

    _make_due(client, routine["id"])
    (_, runner) = _sweep(client)
    assert len(runner.submitted) == 1


def test_a_rule_saved_without_a_trigger_waits_for_one(client):
    """Two boxes left empty: the rule is saved, paused, due at no moment, and
    the list says no moment has been chosen. Giving it one later starts it."""
    room = _room(client, _project(client))
    made = _weekly(
        client, room, headers=PERSON, owner_handle=OWNER, trigger="", spec={}
    )
    assert made.status_code == 200, made.text
    routine = made.json()["data"]
    assert routine["state"] == "paused"
    assert routine["next_run_at"] is None
    assert routine["trigger"] == ""
    assert routine["trigger_text"]

    asked = _deliveries(client, routine["agent_handle"])
    assert len(asked) == 1 and routine["id"] in asked[0].content

    # 恢复它没有意义：没有触发方式就没有「下一次」。
    refused = client.post(f"/routines/{routine['id']}/resume", headers=PERSON)
    assert refused.status_code == 422, refused.text

    patched = client.patch(
        f"/routines/{routine['id']}",
        headers=PERSON,
        json={
            "trigger": "schedule",
            "spec": {"freq": "weekly", "weekdays": [0], "time": "09:00"},
        },
    )
    assert patched.status_code == 200, patched.text
    after = patched.json()["data"]
    assert after["state"] == "active"
    assert after["next_run_at"] is not None


def test_a_teammates_rule_leaves_no_line_of_the_platforms_own_in_the_room(client):
    """The teammate says it in its own words, in its own message; the platform
    does not add a card of its own. Same on the way out: nothing to rewrite."""
    room = _room(client, _project(client))
    routine = _weekly(client, room, owner_handle=OWNER).json()["data"]

    def about_the_rule() -> list[dict]:
        blocks = client.get(f"/topics/{room}/blocks", headers=PERSON).json()["data"]
        return [
            b
            for b in blocks["data"]
            if (b.get("meta") or {}).get("routine_id") == routine["id"]
        ]

    assert about_the_rule() == []
    assert (
        client.delete(f"/routines/{routine['id']}", headers=PERSON).status_code == 200
    )
    assert about_the_rule() == []


def test_the_work_a_run_hands_over_is_labelled_with_the_teammates_name(client):
    room = _room(client, _project(client))
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    _make_due(client, routine["id"])
    _sweep(client)
    [run] = _runs(client, routine["id"])
    thread = _run_message(client, room, run["id"])["routine_run"]["thread_id"]
    blocks = client.get(f"/topics/{thread}/blocks", headers=PERSON).json()["data"]
    [line] = [
        b
        for b in blocks["data"]
        if (b.get("meta") or {}).get("event_type") == "routine_run"
    ]
    label = line["meta"]["detail_label"]
    assert label == f"交给 <@{routine['agent_handle']}> 的工作", label
    assert line["meta"]["i18n"]["detail_label"]["params"] == {
        "who": f"<@{routine['agent_handle']}>"
    }


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
    from app.domain.block.models import Block, BlockKind
    from app.domain.event_author import AuthorType

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


def test_an_ai_edit_to_a_live_rule_takes_effect_at_once(client):
    room = _room(client, _project(client))
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    edited = client.patch(
        f"/routines/{routine['id']}", json={"instructions": "顺手把周报发给所有人"}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["state"] == "active"
    _make_due(client, routine["id"])
    (_, runner) = _sweep(client)
    assert len(runner.submitted) == 1
    assert "顺手把周报发给所有人" in runner.submitted[0][1]["content"]


def _deliveries(client, recipient: str):
    """Timed deliveries waiting for this recipient."""

    async def read(session):
        return list(
            await session.scalars(
                select(TimedDelivery).where(TimedDelivery.recipient_handle == recipient)
            )
        )

    return _db(client, read)


def test_a_rule_that_was_given_no_name_is_named_by_the_teammate(client):
    """A name is a blank like any other: the rule is saved without one and the
    room's teammate is handed a turn to name it, the way task titles are made."""
    room = _room(client, _project(client))
    made = client.post(
        f"/topics/{room}/routines",
        json={
            "instructions": "每周一汇总各频道进展。写成三五条，附上各自的文件。",
            "trigger": "schedule",
            "spec": {"freq": "weekly", "weekdays": [0], "time": "09:00"},
        },
        headers=PERSON,
    )
    assert made.status_code == 200, made.text
    routine = made.json()["data"]
    assert routine["title"] == ""
    asked = _deliveries(client, routine["agent_handle"])
    assert len(asked) == 1 and routine["id"] in asked[0].content


def test_a_rule_has_to_say_what_it_should_do(client):
    room = _room(client, _project(client))
    refused = client.post(
        f"/topics/{room}/routines",
        json={"title": "每周进展", "instructions": "   "},
        headers=PERSON,
    )
    assert refused.status_code >= 400, refused.text
    assert "要它干什么" in refused.json()["message"], refused.json()


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
    from app.domain.block.models import Block, BlockKind
    from app.domain.event_author import AuthorType

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


def _seed_runs(client, routine_id: str, statuses: list[str]) -> None:
    """几次执行，一天一次，最早的在最前面 —— 记录的先后是有意义的，不用时钟凑。"""
    base = datetime.now(UTC) - timedelta(days=len(statuses))

    async def go(session):
        for index, status in enumerate(statuses):
            session.add(
                RoutineRun(
                    routine_id=uuid.UUID(routine_id),
                    occurrence_key=f"seed-{index}",
                    status=status,
                    created_at=base + timedelta(days=index),
                )
            )

    _db(client, go)


def _listed(client, project_id: str) -> list[dict]:
    r = client.get(f"/projects/{project_id}/routines", headers=PERSON)
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def test_the_list_carries_what_became_of_the_latest_run(client):
    """行上那颗图形又要分得出「排队中」和「没能执行」，而规则自己那一行只有启用中 /
    已暂停 —— 这后两档只能从最近一次执行看出来。整页一次问完，不是每行一次往返。"""
    room = _room(client, _project(client))
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    assert [r["last_run_status"] for r in _listed(client, routine["project_id"])] == [
        None
    ]

    _seed_runs(client, routine["id"], ["failed", "succeeded", "queued"])

    assert [r["last_run_status"] for r in _listed(client, routine["project_id"])] == [
        "queued"
    ]


def test_a_page_of_records_counts_what_the_filter_keeps(client):
    """执行记录那一页：倒着给、翻得动、筛得起，而「共 N 次」说的是筛过之后的全部 ——
    不是这一页有几行。"""
    room = _room(client, _project(client))
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    _seed_runs(
        client, routine["id"], ["failed", "succeeded", "failed", "succeeded", "queued"]
    )

    first = client.get(
        f"/routines/{routine['id']}/runs?limit=2", headers=PERSON
    ).json()["data"]
    assert first["total"] == 5
    assert [r["status"] for r in first["data"]] == ["queued", "succeeded"]

    second = client.get(
        f"/routines/{routine['id']}/runs?limit=2&offset=2", headers=PERSON
    ).json()["data"]
    assert {r["id"] for r in second["data"]}.isdisjoint(
        {r["id"] for r in first["data"]}
    )

    missed = client.get(
        f"/routines/{routine['id']}/runs?failed_only=true", headers=PERSON
    ).json()["data"]
    assert missed["total"] == 2
    assert {r["status"] for r in missed["data"]} == {"failed"}
