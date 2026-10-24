"""Standing work: a saved rule wakes the room's teammate, once per moment or
event, and whoever set it hears how each run went."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.domain.delivery.models import Delivery, NativeInput, TimedDelivery
from app.domain.room_task.models import Task, TaskStatus
from app.domain.routine import service as routine_service
from app.domain.routine.models import Routine, RoutineRun
from app.domain.routine.service import REPORT_TIMEOUT, sweep
from app.domain.routine.service._shared import STILL_RUNNING
from app.domain.usage.ledger import Ledger, payer_for_project
from app.domain.user.models import User
from tests.integration.conftest import (
    join_project_team,
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


def _read_run(client, room, routine, read_ago, *, failure=None):
    """A run whose input the teammate's session read ``read_ago``; ``failure``:
    the work it was read into then failed, as its runner reported. Returns the
    run and the work it was read into."""
    _make_due(client, routine["id"])
    _sweep(client)
    run = _runs(client, routine["id"])[0]

    async def read(session):
        run_row = await session.get(RoutineRun, uuid.UUID(run["id"]))
        delivery = await session.scalar(
            select(Delivery).where(Delivery.event_id == run_row.delivery_event_id)
        )
        at = datetime.now(UTC) - read_ago
        attempt = delivery.attempt_id or uuid.uuid4()
        delivery.attempt_id = attempt
        delivery.state = "received"
        work = uuid.uuid4()
        session.add(
            NativeInput(
                project_id=uuid.UUID(routine["project_id"]),
                conversation_id=uuid.UUID(room),
                recipient_handle=delivery.recipient_handle,
                harness="claude-code",
                native_session_id="sess-routine",
                input_id=uuid.uuid4(),
                work_id=work,
                execution_work_id=work,
                delivery_id=delivery.id,
                attempt_id=attempt,
                registered_at=at,
                accepted_at=at,
                echoed_at=at,
                failed_at=datetime.now(UTC) if failure else None,
                failure=failure,
            )
        )
        return work

    return run, _db(client, read)


def test_a_run_its_session_read_is_running_until_it_reports(client):
    """Seen on dev: the run was failed two minutes after it was handed over,
    while the teammate was still writing the summary. Read is started; only a
    report, a failure its runner reported, or the report clock ends it."""
    room = _room(client, _project(client))
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    run, _ = _read_run(client, room, routine, timedelta(minutes=10))

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
    run, _ = _read_run(client, room, routine, REPORT_TIMEOUT + timedelta(minutes=1))

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


def test_a_run_read_into_work_that_failed_fails_with_its_reason(client):
    from app.domain.block.models import Block, BlockKind
    from app.domain.event_author import AuthorType

    room = _room(client, _project(client))
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    _, work = _read_run(
        client, room, routine, timedelta(minutes=1), failure="api_error"
    )

    async def the_room_was_told(session):
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
                turn_id=work,
            )
        )

    _db(client, the_room_was_told)
    _sweep(client)
    run = _runs(client, routine["id"])[0]
    assert run["status"] == "failed"
    assert "模型接口连续报错" in run["error"]


def test_a_run_whose_session_failed_without_a_word_to_the_room_still_fails(client):
    room = _room(client, _project(client))
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    _read_run(client, room, routine, timedelta(minutes=1), failure="exited")

    _sweep(client)
    run = _runs(client, routine["id"])[0]
    assert run["status"] == "failed"
    assert "exited" in run["error"]


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


def _close_task(client, project, room, title, *, by=None, ago=timedelta(0)):
    async def go(session):
        session.add(
            Task(
                id=uuid.uuid4(),
                project_id=uuid.UUID(project),
                room_id=uuid.UUID(room),
                title=title,
                status=TaskStatus.closed,
                closed_at=datetime.now(UTC) - ago,
                created_by=by,
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


def _seed_spend(client, routine, credits_by_run: dict[int, float]) -> None:
    """给这条规则的每一次执行记一笔花费，键是第几次（0 是最早那次）。

    走的是真实的记账入口（`Ledger.record`）：花费写在 `resource_usage` 上、带着那
    一次执行的轮次 id（`turn_id`），和线上跑完一轮时一模一样。
    """
    runs = _runs(client, routine["id"])
    project_id = uuid.UUID(routine["project_id"])

    async def go(session):
        for index, credits in credits_by_run.items():
            turn_id = uuid.uuid4()
            # 记录是倒着给的：第 0 次在最后一行。
            run_row = await session.get(
                RoutineRun, uuid.UUID(runs[len(runs) - 1 - index]["id"])
            )
            run_row.turn_id = turn_id
            await Ledger(session).record(
                await payer_for_project(session, project_id),
                credits=credits,
                model="example-model",
                input_tokens=10,
                output_tokens=10,
                cost_usd=credits * 0.01,
                route="gateway",
                turn_id=turn_id,
            )

    _db(client, go)


def test_a_rule_says_what_it_cost_this_month(client):
    """一条规则花了多少，列表上就写着 —— 它花的是项目所在团队的额度，不记到谁头上，
    所以这是「这个月团队为这条规则出了多少」唯一看得见的地方。

    记的是每一次执行各自的花费（账本按轮次记），加起来才是这条规则的数。"""
    room = _room(client, _project(client))
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    assert _listed(client, routine["project_id"])[0]["credits_this_month"] == 0

    _seed_runs(client, routine["id"], ["succeeded", "succeeded", "succeeded"])
    _seed_spend(client, routine, {0: 3.0, 1: 0.5, 2: 10.0})

    listed = _listed(client, routine["project_id"])[0]
    assert listed["credits_this_month"] == pytest.approx(13.5)

    # 每一次自己的那点也在，按同一本账回答。
    page = client.get(f"/routines/{routine['id']}/runs", headers=PERSON).json()["data"]
    assert page["credits_this_month"] == pytest.approx(13.5)
    by_status = [row["credits"] for row in page["data"]]
    assert by_status == [pytest.approx(10.0), pytest.approx(0.5), pytest.approx(3.0)]


def test_a_run_that_has_not_settled_has_no_figure_yet(client):
    """刚跑完的一次还读不到花费（网关那边要几秒到几十秒才落账），这时是「没有数」
    而不是 0 —— 0 是在说「这一轮没花钱」，两件事。"""
    room = _room(client, _project(client))
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    _seed_runs(client, routine["id"], ["running"])

    page = client.get(f"/routines/{routine['id']}/runs", headers=PERSON).json()["data"]
    assert page["data"][0]["credits"] is None
    # 还没跑完的那一次不进合计；这一条规则因此是 0 点。
    assert page["credits_this_month"] == 0


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


def test_a_moment_that_comes_while_the_last_run_is_still_going_is_skipped(client):
    """Runs of one rule do not pile up: a moment that comes while the last
    run has not ended is recorded as skipped, and nobody is woken for it."""
    room = _room(client, _project(client))
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    _make_due(client, routine["id"], minutes_ago=2)
    _sweep(client)
    _make_due(client, routine["id"], minutes_ago=1)

    (_, runner) = _sweep(client)

    assert runner.submitted == []
    runs = sorted(_runs(client, routine["id"]), key=lambda r: r["scheduled_for"])
    assert [r["status"] for r in runs] == ["queued", "skipped"]
    assert runs[1]["error"] == STILL_RUNNING


def test_a_task_the_rules_own_teammate_opened_does_not_fire_it(client):
    """A run that opens a task and closes it does not set the rule off again."""
    project = _project(client)
    room = _room(client, project)
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
    _close_task(client, project, room, "队友自己开的", by=rule["agent_handle"])

    (_, runner) = _sweep(client)

    assert runner.submitted == []


def test_an_event_committed_after_a_sweep_with_an_earlier_time_still_fires(client):
    """A task closed just before a sweep but committed after it carries an
    earlier time than the newest event that sweep saw; it is still news."""
    project = _project(client)
    room = _room(client, project)
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
    # The rule has been waiting a while: events from before now are its news.
    _db(
        client,
        lambda s: s.execute(
            update(Routine)
            .where(Routine.id == uuid.UUID(rule["id"]))
            .values(event_cursor=datetime.now(UTC) - timedelta(minutes=10))
        ),
    )
    _close_task(client, project, room, "先提交的")
    _sweep(client)

    _close_task(client, project, room, "晚提交的", ago=timedelta(seconds=30))
    (_, runner) = _sweep(client)

    assert len(runner.submitted) == 1
    assert "晚提交的" in runner.submitted[0][1]["content"]
    assert len(_runs(client, rule["id"])) == 2


def test_an_edit_to_an_event_rule_keeps_what_happened_before_it(client):
    project = _project(client)
    room = _room(client, project)
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
    _close_task(client, project, room, "改规则前关的")
    edited = client.patch(
        f"/routines/{rule['id']}", json={"title": "进度表"}, headers=PERSON
    )
    assert edited.status_code == 200, edited.text

    (_, runner) = _sweep(client)

    assert len(runner.submitted) == 1


def test_the_owner_hears_of_the_hourly_limit_once(client):
    project = _project(client)
    room = _room(client, project)
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
    over = routine_service.EVENT_RUNS_PER_HOUR + 3
    for i in range(over):
        _close_task(client, project, room, f"任务 {i}")

    (_, runner) = _sweep(client)
    _close_task(client, project, room, "又一个")
    _sweep(client)

    assert len(runner.submitted) == routine_service.EVENT_RUNS_PER_HOUR
    inbox = client.get(f"/projects/{project}/alerts", headers=PERSON).json()["data"][
        "data"
    ]
    assert len([n for n in inbox if rule["title"] in n["title"]]) == 1, inbox


def test_one_rule_that_cannot_fire_does_not_hold_back_the_others(client, monkeypatch):
    room = _room(client, _project(client))
    broken = _weekly(client, room, headers=PERSON, title="坏的").json()["data"]
    fine = _weekly(client, room, headers=PERSON, title="好的").json()["data"]
    _make_due(client, broken["id"])
    _make_due(client, fine["id"])
    fire = routine_service.scheduling._fire

    async def fails_for_one(session, routine, **kw):
        if str(routine.id) == broken["id"]:
            raise RuntimeError("坏规则")
        return await fire(session, routine, **kw)

    monkeypatch.setattr(routine_service.scheduling, "_fire", fails_for_one)

    (_, runner) = _sweep(client)

    assert len(runner.submitted) == 1
    assert _runs(client, broken["id"]) == []
    after = client.get(f"/routines/{broken['id']}", headers=PERSON).json()["data"]
    assert datetime.fromisoformat(after["next_run_at"]) > datetime.now(UTC)


def test_a_teammate_makes_only_someone_in_the_channel_a_rules_owner(client):
    """Each run reads its owner's private memory and answers in the channel,
    so a teammate cannot make someone outside the channel the owner, nor bring
    them in to be one."""
    project = _project(client)
    room = _room(client, project)
    join_project_team(client, project, "bob")

    refused = _weekly(client, room, owner_handle="bob")

    assert refused.status_code == 422, refused.text
    members = client.get(f"/topics/{room}/members", headers=PERSON).json()["data"]
    assert "bob" not in str(members)


def test_only_a_person_resumes_a_rule(client):
    room = _room(client, _project(client))
    routine = _weekly(client, room, owner_handle=OWNER).json()["data"]
    client.post(f"/routines/{routine['id']}/pause")

    assert client.post(f"/routines/{routine['id']}/resume").status_code == 403
    assert (
        client.post(f"/routines/{routine['id']}/resume", headers=PERSON).status_code
        == 200
    )


def test_a_teammates_rule_keeps_its_owners_time(client):
    """「每周一九点」是主人的九点：队友不写时区，就用主人的。"""
    room = _room(client, _project(client))
    _db(
        client,
        lambda s: s.execute(
            update(User).where(User.username == OWNER).values(timezone="Europe/Berlin")
        ),
    )
    body = {"owner_handle": OWNER}
    made = client.post(
        f"/topics/{room}/routines",
        json={
            "title": "每周进展",
            "instructions": "整理本周进展",
            "trigger": "schedule",
            "spec": {"freq": "weekly", "weekdays": [0], "time": "09:00"},
            **body,
        },
    )

    assert made.status_code == 200, made.text
    assert made.json()["data"]["timezone"] == "Europe/Berlin"
