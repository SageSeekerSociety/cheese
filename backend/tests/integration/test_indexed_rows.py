"""The question and wait lookups find their rows, and find them by index.

`GET /topics` and the board ask, for every room or task of a project at once,
which stop on an open question, which wait on a failed turn, which on a
machine. Each looks for a few rows in a table of every block on the platform, so
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

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.waits import MemberWaits, StuckCard
from app.domain.project.models import Project
from app.domain.room_task.models import Task
from app.domain.room_task.repositories import TaskRepository
from app.domain.run_record.models import RunRecord
from app.domain.topic.models import Topic, TopicKind
from tests.integration.conftest import a_team

INDEXES = {
    "ix_blocks_questions",
    "ix_blocks_machine_events",
    "ix_blocks_failed_turns",
    "ix_blocks_queued_messages",
    "ix_blocks_conversation_eid",
    "ix_blocks_coalesced",
    "ix_blocks_last_said",
    "ix_blocks_unanswered",
    "ix_blocks_agent_checks",
    "ix_blocks_upgraded_to_topic_id",
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
    """Rooms and tasks with one of each case, over a few thousand blocks.

    The bulk sits inside the seven days the wait reads look back over, and
    behind the blocks the cases are built from. Inside the window, or the sort
    those reads do is free, the partial index is dearer than the index already
    there, and the plan says which path is cheapest instead of whether the
    index can be used at all — the two things these assertions exist to tell
    apart. Behind the cases, because the bulk is signed by the agent handle: a
    bulk block newer than a question in the same conversation reads as the asker
    speaking again, which `_awaiting_an_answer` takes for the question
    withdrawn."""
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
            # The machine side, recorded rather than said.
            RunRecord(
                project_id=project.id,
                conversation_id=rooms["machine"].id,
                kind="device_waiting",
                severity="warn",
                content="x",
                created_at=now - hour,
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
            "now()-interval '2 hours'-(g||' minutes')::interval,now() "
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


async def _generic_plan(
    conn, name: str, sql: str, params, *, ordered: bool = False
) -> str:
    """The plan PostgreSQL gives `sql` when it plans it without its values.

    `ordered` also takes the sort away, for a probe whose index earns its keep by
    the order the scan comes back in rather than by holding few rows. Without the
    sort the ordered scan is the only path left to that query, which is what
    "usable at all" means for that read. The other probes keep theirs: those
    indexes are selective, and the planner reaches for them on cost alone.
    """
    if ordered:
        await conn.exec_driver_sql("SET LOCAL enable_sort = off")
    try:
        await conn.exec_driver_sql(f"PREPARE {name} AS {sql}")
        await conn.exec_driver_sql("SET LOCAL plan_cache_mode = force_generic_plan")
        args = ", ".join(_literal(value) for value in params)
        rows = (await conn.exec_driver_sql(f"EXPLAIN EXECUTE {name}({args})")).all()
        await conn.exec_driver_sql(f"DEALLOCATE {name}")
        return "\n".join(row[0] for row in rows)
    finally:
        if ordered:
            await conn.exec_driver_sql("SET LOCAL enable_sort = on")


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

        assert asked == {rooms["asks"].id: "u1", tasks["asks"].id: "u1"}
        assert {room: [w.reason for w in ws] for room, ws in waits.items()} == {
            rooms["failed"].id: ["failed"],
            rooms["machine"].id: ["device_waiting"],
        }

        expected = {
            "ix_blocks_questions": lambda sql: (
                "options" in sql and "DISTINCT ON (blocks.conversation_id)" in sql
            ),
            "ix_blocks_machine_events": lambda sql: "'environment_repaired'" in sql,
            "ix_blocks_failed_turns": lambda sql: "'severity') = 'error'" in sql,
            # 「谁最后说过话」和「谁被点名还没回答」这两条，谓词是字面量，所以它们在
            # sql 里看得见条件本身 —— 反过来，一条退回绑定参数的查询既不该被这个
            # 挑选器选中，也证不出索引。
            "ix_blocks_last_said": lambda sql: (
                "DISTINCT ON (blocks.conversation_id, blocks.author)" in sql
            ),
            "ix_blocks_unanswered": lambda sql: "'mentioned'" in sql,
        }
        conn = await session.connection()
        for index, picks in expected.items():
            # Matched on the parameters too: a query that went back to a bound
            # key carries 'options' there, and must fail on its plan, not here.
            found = [(sql, params) for sql, params in seen if picks(f"{sql} {params}")]
            assert len(found) == 1, f"expected one query for {index}, got {found}"
            sql, params = found[0]
            plan = await _generic_plan(
                conn,
                f"probe_{index}",
                sql,
                params,
                # Most of the table is agent-signed messages, so this index is
                # not selective: what it buys is the order the scan comes back
                # in, and at fixture size the sort it saves is cheap — the
                # planner takes the index it already has and sorts, which says
                # which path is cheapest today and not whether this one can be
                # used at all. Taking the sort away leaves the ordered scan as
                # the only path to this query.
                ordered=index == "ix_blocks_last_said",
            )
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


@pytest.mark.anyio
async def test_a_stuck_card_waits_on_the_agent_and_neither_read_scans_every_block(
    db_factory,
):
    """For each room whose card is stuck, `GET /topics` asks when an agent was
    handed the fix and when an agent last touched the room. A busy room's week
    is mostly its agent's own lines, so neither may be answered by reading the
    blocks of every room there is."""
    async with db_factory() as session:
        seeded = await _seed(session)
        now, room, task = (
            seeded["now"],
            seeded["rooms"]["quiet"],
            seeded["tasks"]["silent"],
        )

        def on_task(by, *, meta, ago):
            platform = by == "platform"
            return Block(
                project_id=room.project_id,
                conversation_id=task.id,
                kind=BlockKind.event if platform else BlockKind.message,
                author_type=AuthorType.platform if platform else AuthorType.participant,
                author=by,
                content="x",
                meta=meta,
                created_at=now - ago,
            )

        touched = now - timedelta(minutes=5)
        session.add_all(
            [
                on_task(
                    "platform",
                    meta={"event_type": "ci_failed", "severity": "error"},
                    ago=timedelta(minutes=30),
                ),
                on_task("cheese-builder", meta={}, ago=timedelta(minutes=5)),
            ]
        )
        await session.flush()
        await session.execute(text("ANALYZE blocks"))
        await session.execute(text("SET LOCAL enable_seqscan = off"))

        with statements(session) as seen:
            waits = await MemberWaits(session).for_rooms(
                [room.id],
                now=now,
                stuck_rooms={room.id: StuckCard(kind="check", pr=7)},
            )

        [wait] = waits[room.id]
        assert (wait.member, wait.reason, wait.pr) == ("cheese-builder", "check", 7)
        assert abs(wait.since - touched) < timedelta(seconds=1)

        conn = await session.connection()
        [events] = [(sql, p) for sql, p in seen if "'ci_failed'" in sql]
        plan = await _generic_plan(conn, "probe_agent_checks", *events)
        assert "ix_blocks_agent_checks" in plan, plan
        for i, (sql, params) in enumerate(seen):
            plan = await _generic_plan(conn, f"probe_wait_{i}", sql, params)
            assert "Seq Scan on blocks" not in plan, f"{sql}\n{plan}"
        await session.rollback()


@pytest.mark.anyio
async def test_a_whole_room_read_finds_its_conversations_blocks_by_index(db_factory):
    """A read across a room — its own line, its tasks', its 支线' — goes to
    those conversations' blocks, not through every block of every room."""
    async with db_factory() as session:
        seeded = await _seed(session)
        # A room with a task of its own, among rooms that hold most of the
        # blocks: reading those is the whole table, whichever way it is done.
        room = seeded["rooms"]["answered"]
        task = Task(project_id=room.project_id, room_id=room.id, title="t")
        session.add(task)
        await session.flush()
        session.add(
            Block(
                project_id=room.project_id,
                conversation_id=task.id,
                kind=BlockKind.message,
                author_type=AuthorType.participant,
                author="u1",
                content="x",
            )
        )
        # Many rooms, as on a live platform: with the fixture's few, any room's
        # share of the table is a large part of it, and reading everything is
        # the right plan for that.
        others = [
            Topic(project_id=room.project_id, title=f"r{i}", kind=TopicKind.topic)
            for i in range(300)
        ]
        session.add_all(others)
        await session.flush()
        await session.execute(
            text(
                "INSERT INTO blocks (project_id,conversation_id,kind,author_type,"
                "author,content,refs,meta,id,created_at,updated_at) "
                "SELECT :pid, (CAST(:rooms AS uuid[]))[g % 300 + 1], 'message',"
                "'participant','u1','x','[]','{}',gen_random_uuid(),now(),now() "
                "FROM generate_series(1,6000) g"
            ),
            {"pid": room.project_id, "rooms": [other.id for other in others]},
        )
        await session.execute(text("ANALYZE blocks"))
        await session.execute(text("SET LOCAL enable_seqscan = off"))

        with statements(session) as seen:
            page = await BlockRepository(session).page_for_topic(
                room.id, limit=50, whole_room=room.id
            )
        assert {block.conversation_id for block in page.items} == {room.id, task.id}

        [(sql, params)] = seen
        conn = await session.connection()
        plan = await _generic_plan(conn, "probe_whole_room", sql, params)
        assert re.search(r"Index Cond: \(conversation_id = ", plan), plan
        await session.rollback()


@pytest.mark.anyio
async def test_deleting_a_room_finds_the_blocks_linking_to_it_by_index(db_factory):
    """A block upgraded into a room links to it, and deleting the room clears
    those links (ON DELETE SET NULL). PostgreSQL finds them with the statement
    below, planned without its value; it must not read every block to do so."""
    async with db_factory() as session:
        await _seed(session)
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        conn = await session.connection()
        plan = await _generic_plan(
            conn,
            "probe_topic_links",
            "UPDATE ONLY blocks SET upgraded_to_topic_id = NULL"
            " WHERE $1::uuid OPERATOR(pg_catalog.=) upgraded_to_topic_id",
            ["00000000-0000-0000-0000-000000000000"],
        )
        assert "ix_blocks_upgraded_to_topic_id" in plan, plan
        await session.rollback()


@pytest.mark.anyio
async def test_the_waiting_messages_come_back_and_are_read_by_their_index(db_factory):
    """The pending-message scan runs on a clock, so it must read its few rows
    from `ix_blocks_queued_messages`, not the table of everything ever said."""
    from app.domain.agent.pending_messages import queued_messages

    async with db_factory() as session:
        seeded = await _seed(session)
        now, room = seeded["now"], seeded["rooms"]["quiet"]
        waiting = {"agent_recipient": {"mentioned": True}, "consumed_turn": None}
        rows = {
            "waiting": waiting,
            "taken": {**waiting, "consumed_turn": "t1"},
            "tried": {**waiting, "prompt_attempts": 1},
            "answered": {**waiting, "answer_to": "q1"},
            "unnamed": {"agent_recipient": {"mentioned": False}, "consumed_turn": None},
        }
        blocks = {
            name: Block(
                project_id=room.project_id,
                conversation_id=room.id,
                kind=BlockKind.message,
                author_type=AuthorType.participant,
                author="u1",
                content=name,
                meta=meta,
                created_at=now - timedelta(minutes=1),
            )
            for name, meta in rows.items()
        }
        session.add_all(blocks.values())
        await session.flush()
        await session.execute(text("ANALYZE blocks"))
        await session.execute(text("SET LOCAL enable_seqscan = off"))

        with statements(session) as seen:
            found = list(
                await session.scalars(queued_messages(now - timedelta(hours=2)))
            )
        assert [block.content for block in found] == ["waiting"]

        [(sql, params)] = seen
        conn = await session.connection()
        plan = await _generic_plan(conn, "probe_queued", sql, params)
        assert "ix_blocks_queued_messages" in plan, f"{sql}\n{plan}"
        await session.rollback()


@pytest.mark.anyio
async def test_a_hook_event_id_is_looked_up_by_index(db_factory):
    """`has_any_eid` runs for every room event and nearly always answers no;
    it must find a yes on either half (a block's own id, or one of the ids a
    coalesced message lists) and read neither half from the whole table."""
    async with db_factory() as session:
        seeded = await _seed(session)
        room = seeded["rooms"]["quiet"]
        session.add_all(
            [
                Block(
                    project_id=room.project_id,
                    conversation_id=room.id,
                    kind=BlockKind.message,
                    author_type=AuthorType.participant,
                    author="u1",
                    content="x",
                    meta=meta,
                )
                for meta in (
                    {"eid": "e-own"},
                    {"eid": "e-first", "eids": ["e-a", "e-b"]},
                )
            ]
        )
        await session.flush()
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        blocks = BlockRepository(session)

        with statements(session) as seen:
            assert await blocks.has_any_eid(room.id, ["e-nope"]) is False
        assert await blocks.has_any_eid(room.id, ["e-nope", "e-own"]) is True
        assert await blocks.has_any_eid(room.id, ["e-b"]) is True
        assert await blocks.has_eid(room.id, "e-own") is True

        conn = await session.connection()
        by_eid = [(sql, p) for sql, p in seen if "'eid') = ANY" in sql]
        coalesced = [(sql, p) for sql, p in seen if "'eids') IS NOT NULL" in sql]
        assert len(by_eid) == 1 and len(coalesced) == 1, seen
        plan = await _generic_plan(conn, "probe_eid", *by_eid[0])
        assert "ix_blocks_conversation_eid" in plan, plan
        # Which index serves the coalesced half depends on the table's
        # statistics (on dev, the partial one); either way not the whole table.
        plan = await _generic_plan(conn, "probe_coalesced", *coalesced[0])
        assert "Seq Scan" not in plan, plan
