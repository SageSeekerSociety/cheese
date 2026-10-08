"""blocks: partial indexes for the two reads behind "who is this room waiting on"

Revision ID: e5b1c7d29f04
Revises: 0c800ff1db2f
Create Date: 2026-10-08

`GET /topics` asks `MemberWaits.for_rooms` for every room of a project at once,
and two of its reads are the expensive part of it: which agent last said
something in each room, and which messages named an agent without a turn having
taken them. Measured on dev (2026-10-08, ~500k blocks, 200 rooms): 195 ms and
209 ms, together 83% of the endpoint's SQL time.

Both predicates are spelled out here as the snapshot a migration must be; the
live copies are `app.domain.block.indexed_rows.LAST_SAID_ROWS` and
`.UNANSWERED_ROWS`, and `tests/integration/test_indexed_rows.py` fails when a
copy drifts from its original.

The handle test is `^@` (`starts_with`) and not LIKE. `%` is a wildcard for the
driver as well as for SQL: SQLAlchemy doubles it for the dialects that escape
it, the asyncpg DDL path does not undo that, so the same text arrived here as
`LIKE 'cheese-%%'` while the model's own DDL said `LIKE 'cheese-%'` — the index
then matched a different string than every query does. `^@` has no spelling to
disagree about, and `cheese-` holds no wildcard.

`ix_blocks_unanswered`'s predicate carries the `CAST`s because that is the shape
the query compiles to, and PostgreSQL matches a partial index by comparing
expression trees, not meanings. The version without them — `(meta ->>
'consumed_turn') IS NULL` — asks the same question in a tree the query never
produces, so the index cannot be proved to apply.

Built CONCURRENTLY for the same reason as b42af333ed1d: `blocks` takes an insert
for every message and every line of agent output, so the write lock a plain
CREATE INDEX holds is not affordable. An invalid index left by a failed
concurrent build is dropped before the build is retried.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5b1c7d29f04"
down_revision: str | Sequence[str] | None = "c46448bdc315"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LAST_SAID = "ix_blocks_last_said"
LAST_SAID_WHERE = (
    "kind = 'message' AND author_type = 'participant'"
    " AND (author = 'cheese' OR author ^@ 'cheese-')"
)
UNANSWERED = "ix_blocks_unanswered"
UNANSWERED_WHERE = (
    "kind = 'message' AND author_type = 'participant'"
    " AND NOT (author = 'cheese' OR author ^@ 'cheese-')"
    " AND CAST((meta -> 'agent_recipient' ->> 'mentioned') AS BOOLEAN)"
    " AND CAST((meta ->> 'consumed_turn') AS VARCHAR) IS NULL"
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
        for name in (LAST_SAID, UNANSWERED):
            if _invalid(name):
                op.drop_index(name, table_name="blocks", postgresql_concurrently=True)
        op.create_index(
            LAST_SAID,
            "blocks",
            ["conversation_id", "author", sa.text("created_at DESC")],
            postgresql_where=sa.text(LAST_SAID_WHERE),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            UNANSWERED,
            "blocks",
            ["conversation_id", "created_at"],
            postgresql_where=sa.text(UNANSWERED_WHERE),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in (LAST_SAID, UNANSWERED):
            op.drop_index(
                name, table_name="blocks", postgresql_concurrently=True, if_exists=True
            )
