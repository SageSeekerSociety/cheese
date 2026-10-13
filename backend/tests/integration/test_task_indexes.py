"""The 领取 and 提交 lookups find their rows, and find them by index.

Every 请求 asks whether this person claimed this 赛题, every visibility decision
asks it again once per candidate 赛题, and a clock asks every 900 s which 领取
is past its deadline with nothing handed in. Those three, and the reads that
walk a 提交's entries and reviews, all ran against tables that never had a
secondary index — `task_membership`, `task_submission`, `task_submission_entry`
and `task_submission_review` came in with a primary key and nothing else, and
their foreign keys all point outward, which PostgreSQL does not index.

`d3f6a2c85b71` builds seven. Two of them are partial, and a partial index is
only used when the query's WHERE reads exactly like the index's, so the sweep's
statement is written from the same literal text as its predicate
(`app.domain.task.indexed_rows`); the answers come out the same either way and
the only place a broken match shows is the plan. These pin both halves: the
answers, and the index behind each.

The plan checked is the generic one, because that is the plan a statement
cached by asyncpg runs on after its first few executions; an EXPLAIN with the
values bound plans like those first few. `SET LOCAL enable_seqscan = off` is
how the plan shows whether the index was *usable* at all rather than whether it
was cheapest today.

The indexes are built by a migration that spells their predicates out again;
the first test fails when that copy and the code's drift apart.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from app.auth.core import Role
from app.auth.domains.task import get_task_roles
from app.auth.domains.team import get_team_roles
from app.domain.task.deadline_scheduler import check_and_fail_expired_deadlines
from app.domain.task.models import (
    TaskMembership,
    TaskSubmission,
    TaskSubmissionEntry,
    TaskSubmissionReview,
)
from app.domain.task.repositories import (
    TaskSubmissionEntryRepository,
    TaskSubmissionRepository,
    TaskSubmissionReviewRepository,
)
from app.domain.team.models import TeamMemberRole, TeamUserRelation
from tests.integration.conftest import a_team

#: What each model declares, and what `d3f6a2c85b71` builds.
TABLES: dict[type, tuple[str, ...]] = {
    TaskMembership: (
        "ix_task_membership_task_member",
        "ix_task_membership_member",
        "ix_task_membership_deadline",
    ),
    TaskSubmission: ("ix_task_submission_membership_id",),
    TaskSubmissionEntry: ("ix_task_submission_entry_submission_id",),
    TaskSubmissionReview: ("ix_task_submission_review_submission_id",),
    TeamUserRelation: ("ix_team_user_relation_team_user",),
}

#: 100 赛题 each claimed by all 50 members: 5000 领取, and one member's name is
#: on 100 of them. That is the shape the auth check reads — one 赛题 × one
#: person — and the shape that makes the pair worth an index of its own rather
#: than the `member_id` one, which would still have 99 rows to look through.
TASKS = 100
MEMBERS = 50

#: The 领取 every lookup in here is about, and the 提交 hanging off it. Its
#: status is one of the two that handed work in, so the sweep leaves it alone.
MEMBER = 8
MEMBERSHIP = 1000000 + MEMBER


@pytest.mark.parametrize("model", list(TABLES), ids=lambda m: m.__name__)
@pytest.mark.anyio
async def test_the_migrated_indexes_are_the_ones_the_code_declares(
    db_factory, model: type
) -> None:
    """Same columns, same order, same predicate as PostgreSQL itself reads them.

    Compared after PostgreSQL has parsed both, so spelling (spaces, casts it
    adds itself) does not count and meaning does.
    """
    names = TABLES[model]
    table = model.__table__  # type: ignore[attr-defined]
    declared = [ix for ix in table.indexes if ix.name in names]
    assert {ix.name for ix in declared} == set(names)
    probe = f"probe_{table.name}"
    async with db_factory() as session:
        await session.execute(text(f'CREATE TEMP TABLE "{probe}" (LIKE {table.name})'))
        for ix in declared:
            ddl = str(CreateIndex(ix).compile(dialect=postgresql.dialect()))
            ddl = ddl.replace(
                f"{ix.name} ON {table.name}", f"{ix.name}_probe ON {probe}"
            )
            await session.execute(text(ddl))
        rows = (
            await session.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE tablename IN (:table, :probe)"
                ),
                {"table": table.name, "probe": probe},
            )
        ).all()
        await session.rollback()
    shape = {name: indexdef.split(" USING ", 1)[1] for name, indexdef in rows}
    for name in names:
        assert name in shape, f"{name} is not on {table.name} — no migration built it"
        assert shape[name] == shape[f"{name}_probe"], (
            f"{name}: the migration built\n  {shape[name]}\n"
            f"but the code declares\n  {shape[f'{name}_probe']}"
        )


async def _seed(session) -> dict[str, object]:
    """`TASKS` 赛题 claimed by all `MEMBERS` members, the way a platform with a
    handful of popular 赛题 looks.

    Every row's status and deadline come off its member number, so what the
    sweep is supposed to find is a property of that number and the expected
    count can be worked out rather than counted by hand: one in five is past its
    deadline, one in nine was deleted, and of the ones left the two statuses
    below are the ones with nothing in hand.
    """
    now = datetime.now(UTC)
    task_ids = list(
        (
            await session.execute(
                text(
                    "INSERT INTO task (id,name,intro,description,creator_id,"
                    "space_id,category_id,submitter_type,approved,"
                    "default_deadline,resubmittable,editable,require_real_name,"
                    "reject_reason,team_locking_policy,created_at,updated_at) "
                    "SELECT nextval('task_seq'), '赛题'||g, '', '', 1, 1, 1, 0, 0,"
                    " 0, false, true, false, '', 'NO_LOCK', now(), now() "
                    "FROM generate_series(1, :tasks) g RETURNING id"
                ),
                {"tasks": TASKS},
            )
        )
        .scalars()
        .all()
    )
    # Inlined rather than bound: an array parameter's type is the driver's to
    # guess, and these are ids this test just read back.
    ids = "{" + ",".join(str(task_id) for task_id in task_ids) + "}"
    await session.execute(
        text(
            "INSERT INTO task_membership (id,task_id,member_id,participant_uuid,"
            "approved,is_team,email,phone,pitch,completion_status,created_at,"
            "updated_at,deadline,deleted_at) "
            f"SELECT 1000000+(t.ord-1)*{MEMBERS}+g, t.task_id, g,"
            " gen_random_uuid(), 1, false, '', '', '',"
            " CASE WHEN g%20=0 THEN 'SUCCESS' WHEN g%10=0 THEN 'NOT_SUBMITTED'"
            " WHEN g%7=0 THEN 'REJECTED_RESUBMITTABLE' ELSE 'PENDING_REVIEW' END,"
            " now(), now(),"
            " CASE WHEN g%5=0 THEN now()-interval '1 day'"
            " ELSE now()+interval '30 days' END,"
            " CASE WHEN g%9=0 THEN now() ELSE NULL END "
            f"FROM unnest('{ids}'::bigint[]) WITH ORDINALITY AS t(task_id, ord)"
            f" CROSS JOIN generate_series(1, {MEMBERS}) g"
        )
    )

    team_id = await a_team(session)
    session.add(
        TeamUserRelation(
            team_id=team_id,
            user_id=MEMBER,
            role=TeamMemberRole.MEMBER,
            created_at=now,
            updated_at=now,
        )
    )
    submission = TaskSubmission(
        membership_id=MEMBERSHIP,
        version=1,
        submitter_id=MEMBER,
        created_at=now,
        updated_at=now,
    )
    session.add(submission)
    await session.flush()
    session.add_all(
        [
            TaskSubmissionEntry(
                task_submission_id=submission.id,
                index=0,
                content_text="x",
                created_at=now,
                updated_at=now,
            ),
            TaskSubmissionReview(
                submission_id=submission.id,
                accepted=False,
                score=0,
                comment="",
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    await session.flush()
    await session.execute(text("ANALYZE task_membership"))
    await session.execute(text("ANALYZE task_submission"))

    # The member numbers the sweep may fail, off the same arithmetic the INSERT
    # above encodes: live, past its deadline, and one of the two statuses with
    # nothing in hand.
    status = {
        g: (
            "SUCCESS"
            if g % 20 == 0
            else "NOT_SUBMITTED"
            if g % 10 == 0
            else "REJECTED_RESUBMITTABLE"
            if g % 7 == 0
            else "PENDING_REVIEW"
        )
        for g in range(1, MEMBERS + 1)
    }
    sweepable = [
        g
        for g, state in status.items()
        if g % 5 == 0
        and g % 9 != 0
        and state in ("NOT_SUBMITTED", "REJECTED_RESUBMITTABLE")
    ]
    return {
        "now": now,
        "task_id": task_ids[0],
        "team_id": team_id,
        "submission_id": submission.id,
        "sweepable": len(sweepable) * TASKS,
    }


def _literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, datetime):
        return "'" + value.isoformat() + "'"
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


async def _assert_read_by_index(session, seen, needle: str, index: str) -> None:
    """The one statement matching `needle` is read by `index`, in the generic plan."""
    found = [(sql, params) for sql, params in seen if needle in sql]
    assert len(found) == 1, f"expected one {needle!r} statement, got {len(found)}"
    sql, params = found[0]
    conn = await session.connection()
    plan = await _generic_plan(conn, f"probe_{index}", sql, params)
    assert index in plan, f"{index} is not used:\n{sql}\n{plan}"


@pytest.mark.anyio
async def test_the_deadline_sweep_reads_only_the_rows_it_can_fail(db_factory):
    """The sweep runs on a clock over every 领取 on the platform, so the rows it
    must not touch are the ones the index exists to skip.

    The statuses in its WHERE are literals shared with the index predicate
    (`app.domain.task.indexed_rows`); with them bound as parameters the same
    index is passed by, which is why this asks for the plan of the statement the
    scheduler actually sends rather than one written for the test.
    """
    async with db_factory() as session:
        seeded = await _seed(session)
        with statements(session) as seen:
            failed = await check_and_fail_expired_deadlines(session)
        assert failed == seeded["sweepable"]

        sweeps = [
            (sql, params)
            for sql, params in seen
            if sql.lstrip().upper().startswith("SELECT")
            and "FROM task_membership" in sql
        ]
        # One page per round, so several rounds of the same statement.
        assert sweeps, "the sweep ran no query"
        assert len({sql for sql, _ in sweeps}) == 1, "the sweep ran two statements"

    # A transaction of its own for the plan: the sweep committed.
    async with db_factory() as probe:
        await probe.execute(text("SET LOCAL enable_seqscan = off"))
        sql, params = sweeps[0]
        conn = await probe.connection()
        plan = await _generic_plan(conn, "probe_sweep", sql, params)
        await probe.rollback()
    assert "ix_task_membership_deadline" in plan, f"{sql}\n{plan}"


@pytest.mark.anyio
async def test_every_per_request_lookup_is_read_by_its_index(db_factory):
    """Both the 请求-time membership checks and the 提交 reads that walk a
    submission's entries and review."""
    async with db_factory() as session:
        seeded = await _seed(session)
        task_id, team_id = seeded["task_id"], seeded["team_id"]
        submission_id = seeded["submission_id"]
        await session.execute(text("SET LOCAL enable_seqscan = off"))

        with statements(session) as seen:
            assert await get_task_roles(session, MEMBER, "task", task_id) == {
                Role.PARTICIPANT
            }
            assert await get_team_roles(session, MEMBER, "team", team_id) == {
                Role.MEMBER
            }
            assert (
                await TaskSubmissionRepository(
                    session
                ).get_latest_version_for_membership(MEMBERSHIP)
                == 1
            )
            assert (
                len(
                    await TaskSubmissionEntryRepository(session).list_by_submission_id(
                        submission_id
                    )
                )
                == 1
            )
            assert (
                await TaskSubmissionReviewRepository(session).exists_by_submission_id(
                    submission_id
                )
                is True
            )

        await _assert_read_by_index(
            session, seen, "FROM task_membership", "ix_task_membership_task_member"
        )
        await _assert_read_by_index(
            session, seen, "FROM team_user_relation", "ix_team_user_relation_team_user"
        )
        await _assert_read_by_index(
            session,
            seen,
            "max(task_submission.version)",
            "ix_task_submission_membership_id",
        )
        await _assert_read_by_index(
            session,
            seen,
            "FROM task_submission_entry",
            "ix_task_submission_entry_submission_id",
        )
        await _assert_read_by_index(
            session,
            seen,
            "FROM task_submission_review",
            "ix_task_submission_review_submission_id",
        )
        await session.rollback()
