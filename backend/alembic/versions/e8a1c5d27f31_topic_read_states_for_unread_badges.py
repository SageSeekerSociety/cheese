"""topic_read_states for 话题级未读角标

Revision ID: e8a1c5d27f31
Revises: d3b8f1c2e5a6
Create Date: 2026-07-02 06:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8a1c5d27f31"
down_revision: str | Sequence[str] | None = "d3b8f1c2e5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "topic_read_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("user_handle", sa.String(length=64), nullable=False),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic_id", "user_handle", name="uq_topic_read_user"),
    )
    op.create_index(
        op.f("ix_topic_read_states_topic_id"),
        "topic_read_states",
        ["topic_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_topic_read_states_user_handle"),
        "topic_read_states",
        ["user_handle"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_topic_read_states_user_handle"), table_name="topic_read_states"
    )
    op.drop_index(op.f("ix_topic_read_states_topic_id"), table_name="topic_read_states")
    op.drop_table("topic_read_states")
