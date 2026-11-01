"""blocks: index the turn a message was handed to and the turn that read it

Revision ID: 321ac342821b
Revises: ce900956b682
Create Date: 2026-11-01 00:30:00

`GET /topics` asks, for the failed turns its rooms wait on, whose turn each
was (`MemberWaits._turn_owners`): the messages whose `prompted_turn` or
`consumed_turn` is one of those turns. Nothing led with either key, so the
planner read every message of the project's rooms since the oldest failure and
tested both keys on each: the slowest statement of the list on dev,
115-213 ms (2026-10-31, 47 statements, 307-577 ms in the database).

One partial expression index per key, holding only the blocks that carry it.
The expressions are spelled out here as the snapshot a migration must be; the
live copy is `app.domain.block.indexed_rows`, and
`tests/integration/test_indexed_rows.py` fails when the two drift apart.

Built CONCURRENTLY, outside alembic's transaction, and an INVALID leftover of
a failed concurrent build is dropped first, as in 9e1b64d280dd.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "321ac342821b"
down_revision: str | Sequence[str] | None = "ce900956b682"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = (
    ("ix_blocks_prompted_turn", "prompted_turn"),
    ("ix_blocks_consumed_turn", "consumed_turn"),
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
        for name, key in INDEXES:
            if _invalid(name):
                op.drop_index(name, table_name="blocks", postgresql_concurrently=True)
            op.create_index(
                name,
                "blocks",
                [sa.text(f"(meta ->> '{key}')")],
                postgresql_where=sa.text(f"(meta ->> '{key}') IS NOT NULL"),
                postgresql_concurrently=True,
                if_not_exists=True,
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, _ in INDEXES:
            op.drop_index(
                name, table_name="blocks", postgresql_concurrently=True, if_exists=True
            )
