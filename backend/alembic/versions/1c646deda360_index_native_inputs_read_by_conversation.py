"""native_inputs: when a conversation last read an input

Revision ID: 1c646deda360
Revises: b7d2e94a1c05
Create Date: 2026-10-20

`(conversation_id, echoed_at)`: a failure in a room clears once a session
there reads an input after it (`block.waits.MemberWaits.unresolved_failures`),
which the room list asks of every room on its page.

CONCURRENTLY, and an invalid index left by a failed build is dropped before
the build is retried.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1c646deda360"
down_revision: str | Sequence[str] | None = "b7d2e94a1c05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

READ = "ix_native_inputs_conversation_read"


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
        if _invalid(READ):
            op.drop_index(
                READ, table_name="native_inputs", postgresql_concurrently=True
            )
        op.create_index(
            READ,
            "native_inputs",
            ["conversation_id", "echoed_at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            READ,
            table_name="native_inputs",
            postgresql_concurrently=True,
            if_exists=True,
        )
