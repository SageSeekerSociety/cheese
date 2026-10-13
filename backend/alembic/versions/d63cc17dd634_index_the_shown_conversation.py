"""The conversation as the room shows it, as an index

Revision ID: d63cc17dd634
Revises: 4195c0bc87ad
Create Date: 2026-10-09

A room opens on one page of its conversation, newest first. Most of a working
room's blocks are an agent's steps, kept for the 现场 and out of the room
(`meta.in_room` false), so a page of 50 rows the room draws used to be read
out of thousands; the client read page after page to fill one screen.
`GET /topics/{id}/blocks?shown=true` now narrows on the server, and this index
holds only those rows, in page order.

The predicate is a snapshot of `app.domain.block.indexed_rows.SHOWN_ROWS`;
`tests/integration/test_indexed_rows.py` fails when the two disagree. Built
CONCURRENTLY because `blocks` takes writes while the previous release serves; an
invalid index left by a failed concurrent build is dropped before the build is
retried.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d63cc17dd634"
down_revision: str | Sequence[str] | None = "4195c0bc87ad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHOWN = "ix_blocks_shown"
SHOWN_WHERE = (
    "kind IN ('message', 'attachment', 'artifact', 'event')"
    " AND COALESCE(meta ->> 'in_room', '') <> 'false'"
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
        if _invalid(SHOWN):
            op.drop_index(SHOWN, table_name="blocks", postgresql_concurrently=True)
        op.create_index(
            SHOWN,
            "blocks",
            ["conversation_id", "created_at", "id"],
            postgresql_where=sa.text(SHOWN_WHERE),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            SHOWN, table_name="blocks", postgresql_concurrently=True, if_exists=True
        )
