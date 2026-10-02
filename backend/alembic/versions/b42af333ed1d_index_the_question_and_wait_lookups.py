"""blocks: partial indexes for open questions, failed turns and machine events

Revision ID: b42af333ed1d
Revises: a6d3f1c9e842
Create Date: 2026-10-02 12:00:00

`GET /topics` and `GET /projects/{id}/tasks` each ask, for every room or every
task of the project at once, which ones stop on an unanswered question
(`BlockRepository._awaiting_an_answer`), and the room list also asks which rooms
wait on a failed turn or on a machine (`MemberWaits`). Each looks for a few
hundred rows in the whole table, and nothing led with their predicates, so the
planner read every block. Measured on dev (2026-10-02, ~330k blocks):

    tasks_awaiting_an_answer   Parallel Seq Scan, 504–513 ms
    rooms_awaiting_an_answer   Parallel Seq Scan, 450–458 ms
    MemberWaits._failed_turns  Parallel Seq Scan, 113–122 ms
    MemberWaits._machine_events  53,532 rows read to keep 49, 70–74 ms

Partial on exactly the predicate each query filters by, so each index holds only
the rows it is for. The predicates are spelled out here as the snapshot a
migration must be; the live copy is `app.domain.block.indexed_rows`, and
`tests/integration/test_indexed_rows.py` fails when the two drift apart.

Built CONCURRENTLY, outside alembic's transaction: `blocks` takes an insert for
every message and every line of agent output, and a plain CREATE INDEX holds
off every one of them for as long as the build reads the table. A concurrent
build that fails (it waits for older transactions under the deploy's 10 s
lock_timeout) leaves an INVALID index behind, which `IF NOT EXISTS` would then
skip forever — so an invalid one is dropped before the build is retried.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b42af333ed1d"
down_revision: str | Sequence[str] | None = "a6d3f1c9e842"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_QUESTION = "kind = 'message' AND (meta ->> 'options') IS NOT NULL"

INDEXES = (
    (
        "ix_blocks_task_questions",
        ["task_id", "created_at DESC"],
        f"task_id IS NOT NULL AND {_QUESTION}",
    ),
    (
        "ix_blocks_room_questions",
        ["topic_id", "created_at DESC"],
        f"task_id IS NULL AND {_QUESTION}",
    ),
    (
        "ix_blocks_machine_events",
        ["topic_id", "created_at"],
        "(meta ->> 'event_type') IN ('machine_provisioning', 'device_waiting', "
        "'sandbox_rebuilt', 'environment_repaired')",
    ),
    (
        "ix_blocks_failed_turns",
        ["topic_id", "created_at"],
        "(meta ->> 'event_type') IN ('turn_failed', 'platform_error')"
        " AND (meta ->> 'severity') = 'error'",
    ),
)


def _invalid(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT NOT i.indisvalid FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
            ),
            {"name": name},
        )
        .scalar()
    )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, columns, where in INDEXES:
            if _invalid(name):
                op.drop_index(name, table_name="blocks", postgresql_concurrently=True)
            op.create_index(
                name,
                "blocks",
                [sa.text(column) for column in columns],
                postgresql_where=sa.text(where),
                postgresql_concurrently=True,
                if_not_exists=True,
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, _, _ in INDEXES:
            op.drop_index(
                name,
                table_name="blocks",
                postgresql_concurrently=True,
                if_exists=True,
            )
