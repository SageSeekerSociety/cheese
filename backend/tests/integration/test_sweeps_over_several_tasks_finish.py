"""A sweep that reaches several tasks or rooms in one pass finishes for all of
them, and alongside another pass over the same ones.

The delivery timer gives up on every instruction that never started, and a
finished task waiting on its write-up instruction closes without it. Two such
tasks given up on in the same pass both close, and both instructions are
marked failed: one task does not take the other's close back with it.

The scheduled-routine sweep and the report of resume instructions that can no
longer start each write into the same rooms' main lines, reaching them in
different orders. Run at the same moment, both finish.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.api.deps import get_chat_service
from app.domain.agent import turn_resume
from app.domain.agent.models import TurnResume
from app.domain.delivery.agent import instance_for_seat
from app.domain.delivery.models import Delivery
from app.domain.delivery.timer import give_up_stale
from app.domain.room_task.closing import CLOSES_TASK
from app.domain.room_task.models import Task, TaskStatus
from app.domain.routine.models import Routine
from app.domain.routine.service import scheduling
from tests.integration.conftest import room_agent_seat, session_auth_headers
from tests.integration.test_the_composer_shows_who_a_message_is_for import _channel


def test_two_tasks_whose_write_up_never_started_both_close(client, stub_hooks):
    project, _, room = _channel(client)
    seat = room_agent_seat(client, room)
    tasks = []
    for _ in range(2):
        made = client.post(
            f"/topics/{room}/tasks", json={}, headers=session_auth_headers("alice")
        )
        assert made.status_code == 200, made.text
        tasks.append(uuid.UUID(made.json()["data"]["id"]))

    async def waiting_for_a_write_up_that_never_started():
        long_ago = datetime.now(UTC) - timedelta(minutes=31)
        for task_id in tasks:  # each accepted on its own, as they would be
            async with client.test_factory() as db:
                await db.execute(
                    update(Task)
                    .where(Task.id == task_id)
                    .values(closing_since=long_ago)
                )
                agent = await instance_for_seat(db, uuid.UUID(project), seat)
                assert agent is not None
                db.add(
                    Delivery(
                        event_id=uuid.uuid4(),
                        recipient_handle=seat,
                        agent_instance_id=agent.id,
                        conversation_id=task_id,
                        dedup_key=str(uuid.uuid4()),
                        type="ROOM_NOTICE",
                        payload={"content": "写一下收尾说明", CLOSES_TASK: True},
                        event_at=long_ago,
                        recorded_at=long_ago,
                        attempts=1,
                        attempt_id=uuid.uuid4(),
                    )
                )
                await db.commit()

    client.portal.call(waiting_for_a_write_up_that_never_started)
    chat = client.app.dependency_overrides[get_chat_service]()

    given_up = client.portal.call(lambda: give_up_stale(client.test_factory, chat=chat))

    async def read_back():
        async with client.test_factory() as db:
            statuses = list(
                await db.scalars(select(Task.status).where(Task.id.in_(tasks)))
            )
            states = list(
                await db.scalars(
                    select(Delivery.state).where(Delivery.conversation_id.in_(tasks))
                )
            )
            return statuses, states

    statuses, states = client.portal.call(read_back)
    assert given_up == 2
    assert statuses == [TaskStatus.closed, TaskStatus.closed]
    assert states == ["failed", "failed"]


def _two_rooms_with_a_routine_due_and_a_resume_failed(client) -> None:
    """Two rooms of one project, each with a scheduled routine due now and a
    resume instruction that can no longer start. Written larger room id first,
    the order the routine sweep then reaches them in; the failed-resume report
    reaches them in its own order."""
    project, _, one = _channel(client)
    other = client.post(
        "/topics",
        json={"project_id": project, "title": "第二个"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    low, high = sorted([uuid.UUID(one), uuid.UUID(other)], key=str)
    seats = {room: room_agent_seat(client, str(room)) for room in (high, low)}

    async def due_and_failed():
        stamp = datetime.now(UTC)
        async with client.test_factory() as db:
            for n, room in enumerate((high, low)):
                ident = uuid.uuid4()
                agent = await instance_for_seat(db, uuid.UUID(project), seats[room])
                assert agent is not None
                db.add(
                    Routine(
                        project_id=uuid.UUID(project),
                        topic_id=room,
                        title=f"例行 {n}",
                        instructions="总结一下",
                        trigger="schedule",
                        spec={"freq": "hourly", "minute": 0},
                        timezone="UTC",
                        state="active",
                        agent_handle=seats[room],
                        owner_handle="alice",
                        created_by="alice",
                        next_run_at=stamp - timedelta(minutes=2 - n),
                    )
                )
                db.add(
                    TurnResume(
                        id=ident,
                        project_id=uuid.UUID(project),
                        conversation_id=room,
                        recipient_handle=seats[room],
                        agent_instance_id=agent.id,
                        failed_turn_id=uuid.uuid4(),
                        reason="rate_limited",
                        said="rate limited",
                        attempt=1,
                        failed_at=stamp - timedelta(minutes=5),
                        due_at=stamp - timedelta(minutes=4),
                        handed_at=stamp - timedelta(minutes=3),
                    )
                )
                db.add(
                    Delivery(
                        event_id=ident,
                        recipient_handle=seats[room],
                        agent_instance_id=agent.id,
                        conversation_id=room,
                        dedup_key=str(ident),
                        type="ROOM_NOTICE",
                        payload={},
                        event_at=stamp,
                        recorded_at=stamp,
                        state="failed",
                        last_error="seat gone",
                    )
                )
                await db.flush()
            await db.commit()

    client.portal.call(due_and_failed)


@pytest.mark.parametrize(
    "routines_first",
    [True, False],
    ids=["routines-then-report", "report-then-routines"],
)
def test_the_routine_sweep_and_the_failed_resume_report_both_finish(
    client, stub_hooks, monkeypatch, routines_first
):
    """Two passes that each write into the same two rooms' main lines, reaching
    them in different orders: the scheduled-routine sweep and the report of
    resume instructions that can no longer start. Either one held part way,
    the other started, both finish."""
    _two_rooms_with_a_routine_due_and_a_resume_failed(client)
    reached, release = asyncio.Event(), asyncio.Event()
    fire = scheduling._fire
    record = turn_resume.record

    async def sweep_held_after_its_first_room(session, routine, **kwargs):
        done = await fire(session, routine, **kwargs)
        if routines_first and not reached.is_set():
            reached.set()
            await release.wait()
        return done

    async def report_held_at_its_first_line(*args, **kwargs):
        if not routines_first and not reached.is_set():
            reached.set()
            await release.wait()
        return await record(*args, **kwargs)

    monkeypatch.setattr(scheduling, "_fire", sweep_held_after_its_first_room)
    monkeypatch.setattr(turn_resume, "record", report_held_at_its_first_line)

    async def routines():
        async with client.test_factory() as db:
            fired = await scheduling._fire_schedules(db)
            await db.commit()
            return fired

    async def report():
        return await turn_resume.report_failed_deliveries(client.test_factory)

    async def race():
        one = asyncio.ensure_future(routines() if routines_first else report())
        await asyncio.wait_for(reached.wait(), 10)
        two = asyncio.ensure_future(report() if routines_first else routines())
        await asyncio.sleep(0.5)
        release.set()
        return await asyncio.wait_for(
            asyncio.gather(one, two, return_exceptions=True), 20
        )

    first, then = client.portal.call(race)
    fired, reported = (first, then) if routines_first else (then, first)
    for result in (fired, reported):
        assert not isinstance(result, BaseException), repr(result)
    assert fired == 2
    assert reported == 2
