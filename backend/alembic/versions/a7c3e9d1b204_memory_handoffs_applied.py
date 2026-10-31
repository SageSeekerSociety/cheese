"""Private trees a seat handed back that the platform has merged

Revision ID: a7c3e9d1b204
Revises: b81e4d7c2a95
Create Date: 2026-10-30

A seat keeps a handed-back private tree until it hears the platform took it;
a merge whose word got lost is otherwise merged again from its old baseline and
undoes what its owner changed since. ``memory_handoffs_applied`` holds the
token of each handoff merged and not yet acknowledged, written in the merge's
own transaction (`app.domain.memory.models.MemoryHandoffApplied`). A new table,
so nothing the serving release reads changes.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c3e9d1b204"
down_revision: str | Sequence[str] | None = "b81e4d7c2a95"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_handoffs_applied",
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("token", name="memory_handoffs_applied_pkey"),
    )


def downgrade() -> None:
    op.drop_table("memory_handoffs_applied")
