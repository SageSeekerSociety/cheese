"""blocks: index the hook event ids a conversation already holds

Revision ID: a6e2f91c4d07
Revises: c71e5a90d4b2
Create Date: 2026-10-07 09:00:00

`BlockRepository.has_any_eid` runs for every room event that lands, to skip a
hook event the conversation already materialized, and nearly always answers
no. With no index on the id it read the whole table for every no: measured on
dev (2026-10-07, 442,504 blocks), a Seq Scan of 873 ms holding a connection,
one of the larger holders the pool instrumentation (#2978) logged.

Two indexes, one per half of the lookup: the id a block carries
(`meta ->> 'eid'`, on nine blocks in ten) and the few coalesced messages that
list several (`meta -> 'eids'`, about 2,300). The expressions are spelled out
here as the snapshot a migration must be; the live copies are
`app.domain.block.indexed_rows.EID` and `COALESCED_ROWS`, and
`tests/integration/test_indexed_rows.py` fails when they drift apart.

Built CONCURRENTLY for the same reason as b42af333ed1d: `blocks` takes an
insert for every message and every line of agent output. An invalid index left
by a failed concurrent build is dropped before the build is retried.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a6e2f91c4d07"
down_revision: str | Sequence[str] | None = "c71e5a90d4b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EID = "ix_blocks_conversation_eid"
COALESCED = "ix_blocks_coalesced"


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
        for name in (EID, COALESCED):
            if _invalid(name):
                op.drop_index(name, table_name="blocks", postgresql_concurrently=True)
        op.create_index(
            EID,
            "blocks",
            ["conversation_id", sa.text("(meta ->> 'eid')")],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            COALESCED,
            "blocks",
            ["conversation_id"],
            postgresql_where=sa.text("(meta -> 'eids') IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in (COALESCED, EID):
            op.drop_index(
                name, table_name="blocks", postgresql_concurrently=True, if_exists=True
            )
