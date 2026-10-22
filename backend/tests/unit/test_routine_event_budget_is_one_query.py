"""`_fire_events` counts recent runs across due event rules in one grouped query.

Previously `_fire_events` issued one `SELECT count(routine_runs.id)` per event
rule inside the loop. It now collects the rules that actually have new events,
skips quiet ticks without querying `routine_runs`, and fetches the per-rule
counts for the last hour in a single `GROUP BY routine_id` query.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import JSON, Column, MetaData, Table, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.domain.routine.models import (
    Routine,
    RoutineRun,
    RoutineState,
    RoutineTrigger,
    RunStatus,
)
from app.domain.routine.service import scheduling
from app.domain.routine.service._shared import EVENT_LOOKBACK, EVENT_RUNS_PER_HOUR
from app.domain.topic.models import Topic, TopicKind, TopicStatus

EXPECTED_SKIP_REASON = (
    f"一小时内已触发 {EVENT_RUNS_PER_HOUR} 次，这一次不执行（防止循环触发）"
)


@pytest.fixture
async def routine_session():
    """In-memory SQLite session with `topics`, `routines`, and `routine_runs`."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    metadata = MetaData()
    for model in (Topic, Routine, RoutineRun):
        Table(
            model.__tablename__,
            metadata,
            *(
                Column(col.name, JSON() if isinstance(col.type, JSONB) else col.type)
                for col in model.__table__.columns
            ),
        )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session, engine
    finally:
        await engine.dispose()


def _make_routine(
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    title: str,
    created_at: datetime,
) -> Routine:
    return Routine(
        id=uuid.uuid4(),
        project_id=project_id,
        topic_id=topic_id,
        title=title,
        instructions="整理进展",
        note="",
        trigger=RoutineTrigger.task_closed.value,
        spec={"scope": "room"},
        timezone="Asia/Shanghai",
        state=RoutineState.active.value,
        agent_handle="cheese-bot",
        owner_handle="user-1",
        created_by="user-1",
        next_run_at=None,
        event_cursor=created_at,
        revision=1,
        created_at=created_at,
        updated_at=created_at,
    )


def _make_run(
    routine_id: uuid.UUID,
    idx: int,
    created_at: datetime,
    status: RunStatus = RunStatus.succeeded,
) -> RoutineRun:
    return RoutineRun(
        id=uuid.uuid4(),
        routine_id=routine_id,
        occurrence_key=f"hist:{idx}:{uuid.uuid4().hex[:8]}",
        trigger_detail="历史事件",
        routine_revision=1,
        scheduled_for=None,
        status=status.value,
        message_id=None,
        delivery_event_id=None,
        turn_id=None,
        summary="",
        outputs=[],
        error=EXPECTED_SKIP_REASON if status is RunStatus.skipped else "",
        created_at=created_at,
        started_at=created_at,
        finished_at=created_at,
        notified=True,
    )


def _install_fake_fire(monkeypatch: pytest.MonkeyPatch, stamp: datetime):
    fired_runs: dict[uuid.UUID, list[RoutineRun]] = {}

    async def fake_fire(
        _session: AsyncSession,
        routine: Routine,
        *,
        occurrence_key: str,
        detail: str,
        scheduled_for: datetime | None,
        skip_reason: str | None = None,
    ) -> RoutineRun:
        run = RoutineRun(
            id=uuid.uuid4(),
            routine_id=routine.id,
            occurrence_key=occurrence_key,
            trigger_detail=detail,
            routine_revision=routine.revision,
            scheduled_for=scheduled_for,
            status=(RunStatus.skipped if skip_reason else RunStatus.queued).value,
            error=skip_reason or "",
            created_at=stamp,
            finished_at=stamp if skip_reason else None,
            outputs=[],
        )
        fired_runs.setdefault(routine.id, []).append(run)
        return run

    monkeypatch.setattr(scheduling, "_fire", fake_fire)
    return fired_runs


@pytest.mark.anyio
async def test_five_rules_three_capped_two_under_cap_query_budget_once(
    routine_session, monkeypatch: pytest.MonkeyPatch
):
    session, engine = routine_session
    stamp = datetime(2026, 10, 18, 10, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(scheduling, "now", lambda: stamp)

    project_id = uuid.uuid4()
    topic_id = uuid.uuid4()
    session.add(
        Topic(
            id=topic_id,
            project_id=project_id,
            title="主频道",
            kind=TopicKind.topic,
            status=TopicStatus.active,
            is_private=False,
            members_only=False,
            archived_with_project=False,
            created_at=stamp - timedelta(days=1),
            updated_at=stamp - timedelta(days=1),
        )
    )

    capped = [
        _make_routine(project_id, topic_id, f"capped-{i}", stamp - timedelta(hours=3))
        for i in range(3)
    ]
    under = [
        _make_routine(project_id, topic_id, f"under-{i}", stamp - timedelta(hours=3))
        for i in range(2)
    ]
    all_rules = [*capped, *under]
    session.add_all(all_rules)

    recent_time = stamp - timedelta(minutes=20)
    expired_time = stamp - timedelta(hours=2)

    # Three rules at or above the per-hour cap (6, 6, 7 active runs in the last hour).
    for idx in range(EVENT_RUNS_PER_HOUR):
        session.add(_make_run(capped[0].id, idx, recent_time, RunStatus.succeeded))
        session.add(_make_run(capped[1].id, idx, recent_time, RunStatus.running))
    for idx in range(EVENT_RUNS_PER_HOUR + 1):
        session.add(_make_run(capped[2].id, idx, recent_time, RunStatus.failed))

    # Under-cap rule 0: zero counted runs in the last hour (absent from GROUP BY,
    # exercising default 0), plus skipped runs in the last hour and old runs > 1h ago.
    for idx in range(EVENT_RUNS_PER_HOUR):
        session.add(_make_run(under[0].id, idx, recent_time, RunStatus.skipped))
        session.add(
            _make_run(under[0].id, 100 + idx, expired_time, RunStatus.succeeded)
        )

    # Under-cap rule 1: EVENT_RUNS_PER_HOUR - 1 runs in the last hour (1 remaining).
    for idx in range(EVENT_RUNS_PER_HOUR - 1):
        session.add(_make_run(under[1].id, idx, recent_time, RunStatus.succeeded))

    await session.commit()

    event_time = stamp - timedelta(seconds=10)

    async def fake_events_for(
        _session: AsyncSession, routine: Routine, _since: datetime
    ):
        return [(f"task:{routine.id}", f"任务《{routine.title}》已关闭。", event_time)]

    monkeypatch.setattr(scheduling, "_events_for", fake_events_for)
    fired_runs = _install_fake_fire(monkeypatch, stamp)

    routine_run_selects: list[str] = []

    def count_routine_run_selects(_conn, _cursor, statement, _params, _context, _many):
        sql = statement.lstrip()
        if sql.upper().startswith("SELECT") and "routine_runs" in sql.lower():
            routine_run_selects.append(sql)

    event.listen(engine.sync_engine, "before_cursor_execute", count_routine_run_selects)
    try:
        fired = await scheduling._fire_events(session)
    finally:
        event.remove(
            engine.sync_engine, "before_cursor_execute", count_routine_run_selects
        )

    assert len(routine_run_selects) == 1, routine_run_selects

    for routine in capped:
        [run] = fired_runs[routine.id]
        assert run.status == RunStatus.skipped.value
        assert run.error == EXPECTED_SKIP_REASON
        # Held back from the clock, so a late commit is still found.
        assert routine.event_cursor == stamp - EVENT_LOOKBACK

    for routine in under:
        [run] = fired_runs[routine.id]
        assert run.status == RunStatus.queued.value
        assert run.error == ""
        # Held back from the clock, so a late commit is still found.
        assert routine.event_cursor == stamp - EVENT_LOOKBACK

    assert fired == 2


@pytest.mark.anyio
async def test_empty_events_continue_without_querying_routine_runs(
    routine_session, monkeypatch: pytest.MonkeyPatch
):
    session, engine = routine_session
    stamp = datetime(2026, 10, 18, 10, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(scheduling, "now", lambda: stamp)

    project_id = uuid.uuid4()
    topic_id = uuid.uuid4()
    cursor_before = stamp - timedelta(hours=3)
    session.add(
        Topic(
            id=topic_id,
            project_id=project_id,
            title="主频道",
            kind=TopicKind.topic,
            status=TopicStatus.active,
            is_private=False,
            members_only=False,
            archived_with_project=False,
            created_at=cursor_before,
            updated_at=cursor_before,
        )
    )
    quiet = _make_routine(project_id, topic_id, "quiet", cursor_before)
    session.add(quiet)
    await session.commit()

    async def no_events(_session: AsyncSession, _routine: Routine, _since: datetime):
        return []

    monkeypatch.setattr(scheduling, "_events_for", no_events)
    fired_runs = _install_fake_fire(monkeypatch, stamp)

    routine_run_selects: list[str] = []

    def count_routine_run_selects(_conn, _cursor, statement, _params, _context, _many):
        sql = statement.lstrip()
        if sql.upper().startswith("SELECT") and "routine_runs" in sql.lower():
            routine_run_selects.append(sql)

    event.listen(engine.sync_engine, "before_cursor_execute", count_routine_run_selects)
    try:
        fired = await scheduling._fire_events(session)
    finally:
        event.remove(
            engine.sync_engine, "before_cursor_execute", count_routine_run_selects
        )

    assert fired == 0
    assert routine_run_selects == []
    assert fired_runs == {}
    assert quiet.event_cursor == cursor_before


@pytest.mark.anyio
async def test_multiple_events_on_one_rule_decrement_budget_within_sweep(
    routine_session, monkeypatch: pytest.MonkeyPatch
):
    session, engine = routine_session
    stamp = datetime(2026, 10, 18, 10, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(scheduling, "now", lambda: stamp)

    project_id = uuid.uuid4()
    topic_id = uuid.uuid4()
    created_at = stamp - timedelta(hours=3)
    session.add(
        Topic(
            id=topic_id,
            project_id=project_id,
            title="主频道",
            kind=TopicKind.topic,
            status=TopicStatus.active,
            is_private=False,
            members_only=False,
            archived_with_project=False,
            created_at=created_at,
            updated_at=created_at,
        )
    )
    rule = _make_routine(project_id, topic_id, "one-slot-left", created_at)
    session.add(rule)
    for idx in range(EVENT_RUNS_PER_HOUR - 1):
        session.add(
            _make_run(rule.id, idx, stamp - timedelta(minutes=15), RunStatus.succeeded)
        )
    await session.commit()

    first_when = stamp - timedelta(seconds=20)
    second_when = stamp - timedelta(seconds=10)

    async def two_events(_session: AsyncSession, _routine: Routine, _since: datetime):
        return [
            ("task:1", "任务《一》已关闭。", first_when),
            ("task:2", "任务《二》已关闭。", second_when),
        ]

    monkeypatch.setattr(scheduling, "_events_for", two_events)
    fired_runs = _install_fake_fire(monkeypatch, stamp)

    fired = await scheduling._fire_events(session)

    assert fired == 1
    first, second = fired_runs[rule.id]
    assert first.status == RunStatus.queued.value
    assert first.error == ""
    assert second.status == RunStatus.skipped.value
    assert second.error == EXPECTED_SKIP_REASON
    assert rule.event_cursor == stamp - EVENT_LOOKBACK
