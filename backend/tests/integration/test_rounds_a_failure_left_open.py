"""The rounds a failure left open stop holding their room's seat (cffd329c35e3).

The bug: a round the API refuses ends without the completion stamp, and the
runner's own terminal stamp was skipped whenever it held a harness background
task at that instant (#2570). The inputs the round read stayed unfinished,
``seat_has_unfinished_input`` read them as outstanding holds, and the seat
deferred every later message — the room went silent and never came back.

The migration's ``upgrade()`` is run the way ``alembic upgrade head`` runs it.
What is checked is what the seat does afterwards: ``seat_has_unfinished_input``
is the predicate that decides whether the next message is deferred or run.
"""

import asyncio
import importlib.util
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.domain.delivery.input_holds import seat_has_unfinished_input
from tests.conftest import seed_user
from tests.integration.conftest import (
    post_project,
    room_agent_seat,
    session_auth_headers,
)

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "cffd329c35e3_end_failed_rounds_and_settle_their_inputs.py"
)

#: What the platform writes under a round's id when it broke.
BROKE = {"event_type": "turn_failed", "severity": "error"}


def _upgrade(client) -> None:
    spec = importlib.util.spec_from_file_location("_mig_failed_rounds", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def _apply(conn) -> None:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()

    async def _run() -> None:
        async with client.test_factory() as s:
            await (await s.connection()).run_sync(_apply)
            await s.commit()

    asyncio.run(_run())


def _project(client) -> tuple[str, dict]:
    headers = {"Authorization": f"Bearer {seed_user(client, 'alice')}"}
    response = post_project(client, json={"name": "Rounds"}, owner="alice")
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"], headers


def _room(client, project_id: str) -> str:
    response = client.post(
        "/topics",
        json={"project_id": project_id, "title": "Room"},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def _task(client, project_id: str, room: str) -> uuid.UUID:
    task_id = uuid.uuid4()

    async def _run() -> None:
        async with client.test_factory() as s:
            await s.execute(
                text(
                    "INSERT INTO tasks (id, project_id, room_id, title, status,"
                    " brief, created_at, updated_at) VALUES (:id, :project, :room,"
                    " 'work', 'open', '', now(), now())"
                ),
                {
                    "id": task_id,
                    "project": uuid.UUID(project_id),
                    "room": uuid.UUID(room),
                },
            )
            await s.commit()

    asyncio.run(_run())
    return task_id


def _turn(
    client,
    room: str,
    *,
    started: datetime,
    stopped: datetime | None = None,
    delivered: bool = True,
    task_id: uuid.UUID | None = None,
    said: tuple[tuple[datetime, str], ...] = (),
    broke_at: datetime | None = None,
) -> uuid.UUID:
    """One round on ``room``'s seat and a block under its id for each
    ``(at, author)`` in ``said``. ``broke_at`` adds the platform event that
    records the round ended in an error — the evidence the migration reads."""
    turn_id = uuid.uuid4()
    seat = room_agent_seat(client, room)

    async def _run() -> None:
        async with client.test_factory() as s:
            await s.execute(
                text(
                    "INSERT INTO agent_turns (id, topic_id, task_id, continuation_id,"
                    " author, content, is_resume, resendable, started_at, delivered_at,"
                    " stopped_at, agent_handle) VALUES (:id, :room, :task, :id, :seat,"
                    " 'hello', false, true, :started, :delivered, :stopped, :seat)"
                ),
                {
                    "id": turn_id,
                    "room": uuid.UUID(room),
                    "task": task_id,
                    "seat": seat,
                    "started": started,
                    "delivered": started if delivered else None,
                    "stopped": stopped,
                },
            )
            project_id = (
                await s.execute(
                    text("SELECT project_id FROM topics WHERE id = :room"),
                    {"room": uuid.UUID(room)},
                )
            ).scalar_one()
            for at, by in said:
                await s.execute(
                    text(
                        "INSERT INTO blocks (id, project_id, topic_id, kind,"
                        " author_type, author, content, refs, turn_id, created_at,"
                        " updated_at) VALUES (:id, :project, :room, 'message',"
                        " 'participant', :by, 'working on it', '[]', :turn, :at, :at)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "project": project_id,
                        "room": uuid.UUID(room),
                        "by": by,
                        "turn": turn_id,
                        "at": at,
                    },
                )
            if broke_at is not None:
                await s.execute(
                    text(
                        "INSERT INTO blocks (id, project_id, topic_id, kind,"
                        " author_type, author, content, refs, meta, turn_id,"
                        " created_at, updated_at) VALUES (:id, :project, :room,"
                        " 'event', 'platform', 'system', '本轮未完成', '[]', :meta,"
                        " :turn, :at, :at)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "project": project_id,
                        "room": uuid.UUID(room),
                        "meta": json.dumps(BROKE),
                        "turn": turn_id,
                        "at": broke_at,
                    },
                )
            await s.commit()

    asyncio.run(_run())
    return turn_id


def _input(
    client,
    room: str,
    *,
    work: uuid.UUID,
    registered: datetime,
    echoed_at: datetime | None = None,
    opened: bool = True,
) -> uuid.UUID:
    """One input row for ``work``: ``echoed_at`` is the session's statement that
    it read the input, and an input that opened its own work is the one whose
    id names it (``input_id = work_id``)."""
    input_id = work if opened else uuid.uuid4()
    seat = room_agent_seat(client, room)

    async def _run() -> None:
        async with client.test_factory() as s:
            project_id = (
                await s.execute(
                    text("SELECT project_id FROM topics WHERE id = :room"),
                    {"room": uuid.UUID(room)},
                )
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO native_inputs (id, project_id, topic_id,"
                    " recipient_handle, harness, native_session_id, input_id, work_id,"
                    " execution_work_id, held_block_ids, released_block_ids,"
                    " block_ids, seen_block_ids, registered_at, echoed_at)"
                    " VALUES (:id, :project, :room, :seat, 'claude_code', :native,"
                    " :input, :work, :exec, '[]', '[]', '[]', '[]', :registered,"
                    " :echoed)"
                ),
                {
                    "id": uuid.uuid4(),
                    "project": project_id,
                    "room": uuid.UUID(room),
                    "seat": seat,
                    "native": str(uuid.uuid4()),
                    "input": input_id,
                    "work": work,
                    "exec": work if echoed_at is not None else None,
                    "registered": registered,
                    "echoed": echoed_at,
                },
            )
            await s.commit()

    asyncio.run(_run())
    return input_id


def _seat_holds(client, room: str) -> bool:
    seat = room_agent_seat(client, room)

    async def _run() -> bool:
        async with client.test_factory() as s:
            return await seat_has_unfinished_input(s, uuid.UUID(room), seat)

    return asyncio.run(_run())


def _stopped_at(client, turn_id: uuid.UUID) -> datetime | None:
    async def _run():
        async with client.test_factory() as s:
            return (
                await s.execute(
                    text("SELECT stopped_at FROM agent_turns WHERE id = :id"),
                    {"id": turn_id},
                )
            ).scalar_one()

    return asyncio.run(_run())


def _input_state(client, input_id: uuid.UUID) -> tuple:
    async def _run():
        async with client.test_factory() as s:
            return (
                await s.execute(
                    text(
                        "SELECT completed_at, terminated_at, termination"
                        " FROM native_inputs WHERE input_id = :id"
                    ),
                    {"id": input_id},
                )
            ).one()

    return asyncio.run(_run())


def test_a_round_recorded_as_failed_stops_holding_its_seat(client):
    project_id, _ = _project(client)
    room = _room(client, project_id)
    now = datetime.now(UTC)
    last_word = now - timedelta(hours=15)
    broke_at = now - timedelta(hours=14)
    round_ = _turn(
        client,
        room,
        started=now - timedelta(hours=20),
        said=((last_word, "alice"),),
        broke_at=broke_at,
    )
    read = _input(
        client,
        room,
        work=round_,
        registered=now - timedelta(hours=20),
        echoed_at=now - timedelta(hours=19),
    )

    assert _seat_holds(client, room)

    _upgrade(client)

    assert not _seat_holds(client, room)
    # The round ends at the last thing it wrote — the failure event itself.
    assert _stopped_at(client, round_) == broke_at
    # The input it read is settled as a terminal outcome, never as a completion:
    # whether the answer inside it was taken is still unknown.
    completed, terminated, termination = _input_state(client, read)
    assert completed is None
    assert terminated == broke_at
    assert termination == "is_error"


def test_an_input_the_round_never_read_is_settled_by_the_round_ending(client):
    project_id, _ = _project(client)
    room = _room(client, project_id)
    now = datetime.now(UTC)
    round_ = _turn(
        client,
        room,
        started=now - timedelta(hours=20),
        broke_at=now - timedelta(hours=14),
    )
    # Nothing says the session ever took it: the round ending is all it waits for.
    never_read = _input(client, room, work=round_, registered=now - timedelta(hours=20))

    assert _seat_holds(client, room)

    _upgrade(client)

    assert not _seat_holds(client, room)
    assert _stopped_at(client, round_) is not None
    completed, terminated, termination = _input_state(client, never_read)
    assert (completed, terminated, termination) == (None, None, None)


def test_rounds_without_that_evidence_keep_their_seat(client):
    project_id, _ = _project(client)
    room = _room(client, project_id)
    task = _task(client, project_id, room)
    now = datetime.now(UTC)

    # Quiet for hours, but nothing recorded that it broke.
    silent = _turn(client, room, started=now - timedelta(hours=20))
    silent_input = _input(
        client,
        room,
        work=silent,
        registered=now - timedelta(hours=20),
        echoed_at=now - timedelta(hours=19),
    )
    # Broke hours ago and wrote an hour ago: its session is still on it.
    working = _turn(
        client,
        room,
        started=now - timedelta(hours=20),
        said=((now - timedelta(hours=1), "alice"),),
        broke_at=now - timedelta(hours=14),
    )
    working_input = _input(
        client,
        room,
        work=working,
        registered=now - timedelta(hours=20),
        echoed_at=now - timedelta(hours=19),
    )
    # On a task's line: the task's own machinery settles it.
    on_a_task = _turn(
        client,
        room,
        started=now - timedelta(hours=20),
        task_id=task,
        broke_at=now - timedelta(hours=14),
    )
    task_input = _input(
        client,
        room,
        work=on_a_task,
        registered=now - timedelta(hours=20),
        echoed_at=now - timedelta(hours=19),
    )
    # Never reached its session: the orphan sweep's to re-send.
    undelivered = _turn(
        client,
        room,
        started=now - timedelta(hours=30),
        delivered=False,
        broke_at=now - timedelta(hours=14),
    )
    undelivered_input = _input(
        client,
        room,
        work=undelivered,
        registered=now - timedelta(hours=30),
        echoed_at=now - timedelta(hours=29),
    )

    _upgrade(client)

    assert _stopped_at(client, silent) is None
    assert _stopped_at(client, working) is None
    assert _stopped_at(client, on_a_task) is None
    assert _stopped_at(client, undelivered) is None
    for input_id in (silent_input, working_input, task_input, undelivered_input):
        completed, terminated, termination = _input_state(client, input_id)
        assert (completed, terminated, termination) == (None, None, None)
    assert _seat_holds(client, room)
