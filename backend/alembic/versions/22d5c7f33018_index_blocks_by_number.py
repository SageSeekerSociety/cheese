"""blocks: no two blocks of a conversation share a number

Revision ID: 22d5c7f33018
Revises: 1a96fb7b05db
Create Date: 2026-10-09

`(conversation_id, seq)`, unique. The trigger that numbers new blocks
(`0dd66b328211`) reads the largest number in the conversation from it, and a
page catching up reads "after n" from it. Built before the trigger, so that read
is one index lookup from the first block numbered, and while `seq` is still
empty, so the build has nothing to sort.

CONCURRENTLY: `blocks` takes an insert for every message and every line of
agent output. An invalid index left by a failed build is dropped before the
build is retried.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "22d5c7f33018"
down_revision: str | Sequence[str] | None = "1a96fb7b05db"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NUMBERED = "ix_blocks_conversation_seq"


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
        if _invalid(NUMBERED):
            op.drop_index(NUMBERED, table_name="blocks", postgresql_concurrently=True)
        op.create_index(
            NUMBERED,
            "blocks",
            ["conversation_id", "seq"],
            unique=True,
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            NUMBERED, table_name="blocks", postgresql_concurrently=True, if_exists=True
        )
