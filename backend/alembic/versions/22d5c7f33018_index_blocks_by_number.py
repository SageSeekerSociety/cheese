"""blocks: index the numbers, for catching up and for counting unread

Revision ID: 22d5c7f33018
Revises: 5f1b7d54bffa
Create Date: 2026-10-09

Two indexes on the number `1a96fb7b05db` gives every block:

- `(conversation_id, seq)`, unique: no two blocks of a conversation share a
  number, and a page catching up reads "after n" straight from it.
- `(conversation_id, kind, seq) INCLUDE (author)`: the unread count, now that a
  read cursor is a number (`4e8f1c1e2b22`). The same shape as
  `ix_blocks_conversation_kind_created`, which counted against a time and still
  serves the release running while this deploy does; it goes once that release
  is gone.

Built after the numbering, so filling 500k numbers did not write into them as
well, and CONCURRENTLY: `blocks` takes an insert for every message and every
line of agent output. An invalid index left by a failed build is dropped
before the build is retried.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "22d5c7f33018"
down_revision: str | Sequence[str] | None = "5f1b7d54bffa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NUMBERED = "ix_blocks_conversation_seq"
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
        for name in (NUMBERED, UNREAD):
            if _invalid(name):
                op.drop_index(name, table_name="blocks", postgresql_concurrently=True)
        op.create_index(
            NUMBERED,
            "blocks",
            ["conversation_id", "seq"],
            unique=True,
            postgresql_concurrently=True,
            if_not_exists=True,
        )
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
        for name in (UNREAD, NUMBERED):
            op.drop_index(
                name, table_name="blocks", postgresql_concurrently=True, if_exists=True
            )
