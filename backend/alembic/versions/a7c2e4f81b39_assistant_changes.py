"""What a 芝士 changed while answering, kept to be undone

Revision ID: a7c2e4f81b39
Revises: e5f1a9c3d7b2
Create Date: 2026-10-21

One row per write a 芝士 makes while answering a question — into a 题目 draft
or a living document — with what it changed and which parts the person undid
(``app.domain.assistant.changes``). It replaces two records that did not reach
each other: the draft writes told on the question's stream only, and the
document writes noted in Valkey for an hour.

A new, empty table: nothing to move.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7c2e4f81b39"
down_revision: str | Sequence[str] | None = "e5f1a9c3d7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistant_changes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("work", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("target", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "undone",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            ondelete="CASCADE",
            name="fk_assistant_changes_user_id_user",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assistant_changes_work", "assistant_changes", ["work", "id"], unique=False
    )
    op.create_index(
        "ix_assistant_changes_user_id", "assistant_changes", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_changes_user_id", table_name="assistant_changes")
    op.drop_index("ix_assistant_changes_work", table_name="assistant_changes")
    op.drop_table("assistant_changes")
