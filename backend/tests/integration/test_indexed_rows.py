"""The question and wait lookups find their rows, and find them by index.

`GET /topics` and the board ask, for every room or task of a project at once,
which stop on an open question, which wait on a failed turn, which on a
machine; every turn starts by finding its room's newest cloud-provisioning
event. Each looks for a few rows in a table of every block on the platform, so
each has a partial index on its own predicate — and a partial index is only
used when the query's WHERE reads exactly like the index's. The answers come
out the same either way, so the only place a broken match shows is the plan.
These pin both halves: the answers, and the index behind each.

The plan checked is the generic one, because that is the plan a statement
cached by asyncpg runs on after its first few executions; an EXPLAIN with the
values bound plans like those first few, and can show the index used for a
filter that binds its key (`Block.meta[...].as_string()`).

The indexes are built by migrations that spell their predicates out again;
the first test fails when that copy and the code's drift apart.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.waits import MemberWaits
from app.domain.project.models import Project
from app.domain.room_task.models import Task
from app.domain.room_task.repositories import TaskRepository
from app.domain.topic.models import Topic, TopicKind
from tests.integration.conftest import a_team

INDEXES = {
    "ix_blocks_cloud_provisioning",
    "ix_blocks_questions",
    "ix_blocks_machine_events",
    "ix_blocks_failed_turns",
}


@pytest.mark.anyio
async def test_the_migrated_indexes_are_the_ones_the_code_declares(db_factory):
    """Same columns, same order, same predicate as PostgreSQL itself reads them.

    Compared after PostgreSQL has parsed both, so spelling (spaces, casts it
    adds itself) does not count and meaning does.
    """
    declared = [ix for ix in Block.__table__.indexes if ix.name in INDEXES]
    assert {ix.name for ix in declared} == INDEXES
    async with db_factory() as session:
        await session.execute(text("CREATE TEMP TABLE probe (LIKE blocks)"))
        for ix in declared:
            ddl = str(CreateIndex(ix).compile(dialect=postgresql.dialect()))
            ddl = ddl.replace(f"{ix.name} ON blocks", f"{ix.name}_probe ON probe")
            await session.execute(text(ddl))
        rows = (
            await session.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE tablename IN ('blocks', 'probe')"
                )
            )
        ).all()
        await session.rollback()
    shape = {name: indexdef.split(" USING ", 1)[1] for name, indexdef in rows}
    for name in INDEXES:
        assert name in shape, f"{name} is not on blocks — no migration built it"
        assert shape[name] == shape[f"{name}_probe"], (
            f"{name}: the migration built\n  {shape[name]}\n"
            f"but the code declares\n  {shape[name + '_probe']}"
        )


async def _seed(session) -> dict[str, object]:
    """Rooms and tasks with one of each case, over a few thousand older blocks
    so the planner has a real table to choose a path through."""
    now = datetime.now(UTC)
    project = Project(team_id=await a_team(session), name="P", owner_handle="o")
    session.add(project)
    await session.flush()
    rooms = {
        name: Topic(project_id=project.id, title=name, kind=TopicKind.topic)
        for name in ("asks", "answered", "failed", "warned", "machine", "quiet")
    }
    session.add_all(rooms.values())
    await session.flush()
    tasks = {
        name: Task(project_id=project.id, room_id=rooms["quiet"].id, title=name)
        for name in ("asks", "answered", "quiet", "silent")
    }
    session.add_all(tasks.values())
    await session.flush()

    def block(room, *, task=None, kind=BlockKind.message, by="u1", meta, ago):
        platform = by == "platform"
        return Block(
            project_id=project.id,
            conversation_id=tasks[task].id if task else rooms[room].id,
            kind=kind,
            author_type=AuthorType.platform if platform else AuthorType.participant,
            author=by,
            content="x",
            meta=meta,
            created_at=now - ago,
        )

    asked = {"options": ["yes", "no"], "asked": "u1"}
    hour = timedelta(hours=1)
    session.add_all(
        [
            block("asks", by="cheese", meta=asked, ago=hour),
            block("answered", by="cheese", meta={**asked, "answered": "yes"}, ago=hour),
            block("quiet", task="asks", by="cheese", meta=asked, ago=hour),
            block(
                "quiet",
                task="answered",
                by="cheese",
                meta={**asked, "answered": "no"},
                ago=hour,
            ),
            block(
                "failed",
                kind=BlockKind.event,
                by="platform",
                meta={"event_type": "turn_failed", "severity": "error"},
                ago=hour,
            ),
            # A warning is the platform carrying on by itself, not a broken turn.
            block(
                "warned",
                kind=BlockKind.event,
                by="platform",
                meta={"event_type": "platform_error", "severity": "warning"},
                ago=hour,
            ),
            block(
                "machine",
                meta={"agent_recipient": {"mentioned": True, "handle": "cheese"}},
                ago=2 * hour,
            ),
            block(
                "machine",
                kind=BlockKind.event,
                by="platform",
                meta={"event_type": "device_waiting"},
                ago=hour,
            ),
            block(
                "quiet",
                kind=BlockKind.event,
                by="platform",
                meta={"event_type": "cloud_provisioning"},
                ago=hour,
            ),
        ]
    )
    await session.flush()
    await session.execute(
        text(
            "INSERT INTO blocks (project_id,conversation_id,kind,author_type,author,"
            "content,refs,meta,id,created_at,updated_at) "
            "SELECT :pid, CASE WHEN g%4=0 THEN CAST(:t AS uuid) ELSE "
            "(ARRAY[CAST(:a AS uuid),CAST(:b AS uuid),CAST(:c AS uuid)])[g%3+1] END,"
            "CASE WHEN g%2=0 THEN 'message' ELSE 'event' END,'participant','cheese',"
            "'x','[]',json_build_object('tool','Bash'),gen_random_uuid(),"
            "now()-interval '30 days'-(g||' minutes')::interval,now() "
            "FROM generate_series(1,4000) g"
        ),
        {
            "pid": project.id,
            "a": rooms["asks"].id,
            "b": rooms["failed"].id,
            "c": rooms["quiet"].id,
            "t": tasks["quiet"].id,
        },
    )
    await session.execute(text("ANALYZE blocks"))
    return {"now": now, "rooms": rooms, "tasks": tasks}


def _literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list | tuple):
        return "'{" + ",".join(f'"{item}"' for item in value) + "}'"
    return "'" + str(value).replace("'", "''") + "'"


async def _generic_plan(conn, name: str, sql: str, params) -> str:
    """The plan PostgreSQL gives `sql` when it plans it without its values."""
    await conn.exec_driver_sql(f"PREPARE {name} AS {sql}")
    await conn.exec_driver_sql("SET LOCAL plan_cache_mode = force_generic_plan")
    args = ", ".join(_literal(value) for value in params)
    rows = (await conn.exec_driver_sql(f"EXPLAIN EXECUTE {name}({args})")).all()
    await conn.exec_driver_sql(f"DEALLOCATE {name}")
    return "\n".join(row[0] for row in rows)


@contextmanager
def statements(session) -> Iterator[list[tuple[str, object]]]:
    seen: list[tuple[str, object]] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append((statement, parameters))

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


@pytest.mark.anyio
async def test_questions_and_waits_come_back_and_each_read_uses_its_index(
    db_factory,
):
    async with db_factory() as session:
        seeded = await _seed(session)
        rooms, tasks = seeded["rooms"], seeded["tasks"]
        room_ids = [room.id for room in rooms.values()]
        # Every path is still open to the planner; this only makes reading the
        # whole table the last resort, so the plan shows whether the partial
        # index was usable at all rather than whether it was cheapest today.
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        with statements(session) as seen:
            asked = await BlockRepository(session).awaiting_an_answer(
                [*room_ids, *(task.id for task in tasks.values())]
            )
            waits = await MemberWaits(session).for_rooms(room_ids, now=seeded["now"])
            history = await BlockRepository(session).turn_history(rooms["quiet"].id)

        assert asked == {rooms["asks"].id: "u1", tasks["asks"].id: "u1"}
        assert {room: [w.reason for w in ws] for room, ws in waits.items()} == {
            rooms["failed"].id: ["failed"],
            rooms["machine"].id: ["device_waiting"],
        }
        assert "cloud_provisioning" in [b.meta.get("event_type") for b in history]

        expected = {
            "ix_blocks_questions": lambda sql: (
                "options" in sql and "DISTINCT ON (blocks.conversation_id)" in sql
            ),
            "ix_blocks_machine_events": lambda sql: "'device_waiting'" in sql,
            "ix_blocks_failed_turns": lambda sql: "'severity') = 'error'" in sql,
            "ix_blocks_cloud_provisioning": lambda sql: "cloud_provisioning" in sql,
        }
        conn = await session.connection()
        for index, picks in expected.items():
            # Matched on the parameters too: a query that went back to a bound
            # key carries 'options' there, and must fail on its plan, not here.
            found = [(sql, params) for sql, params in seen if picks(f"{sql} {params}")]
            assert len(found) == 1, f"expected one query for {index}, got {found}"
            sql, params = found[0]
            plan = await _generic_plan(conn, f"probe_{index}", sql, params)
            assert index in plan, f"{index} is not used:\n{sql}\n{plan}"

        with statements(session) as seen:
            beats = await TaskRepository(session).last_block_at_for_tasks(
                [task.id for task in tasks.values()]
            )
        assert set(beats) == {tasks[name].id for name in ("asks", "answered", "quiet")}
        assert beats[tasks["asks"].id] == seeded["now"] - timedelta(hours=1)
        # Not a partial index, but the same question of whether the read is
        # cheap: each task's newest block, read from the end of its run in the
        # index. A GROUP BY over the tasks' blocks uses this index too, reading
        # every entry forwards, so the direction is what tells them apart.
        [(sql, params)] = seen
        plan = await _generic_plan(conn, "probe_beats", sql, params)
        assert "Backward using ix_blocks_conversation_created_at" in plan, plan
        await session.rollback()
