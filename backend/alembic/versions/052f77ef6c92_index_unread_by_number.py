"""blocks: count unread against a number

Revision ID: 052f77ef6c92
Revises: 5f1b7d54bffa
Create Date: 2026-10-09

`(conversation_id, kind, seq) INCLUDE (author)`: the unread count, now that a
read cursor is a number (`4e8f1c1e2b22`). The same shape as
`ix_blocks_conversation_kind_created`, which counted against a time and still
serves the release running while this deploy does; it goes once that release
is gone. Built after the numbering, so filling 500k numbers did not write into
it as well.

CONCURRENTLY, and an invalid index left by a failed build is dropped before
the build is retried.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "052f77ef6c92"
down_revision: str | Sequence[str] | None = "5f1b7d54bffa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNREAD = "ix_blocks_conversation_kind_seq"


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
        if _invalid(UNREAD):
            op.drop_index(UNREAD, table_name="blocks", postgresql_concurrently=True)
        op.create_index(
            UNREAD,
            "blocks",
            ["conversation_id", "kind", "seq"],
            postgresql_include=["author"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            UNREAD, table_name="blocks", postgresql_concurrently=True, if_exists=True
        )
