"""webhook tokens table (webhook 原语)

One row per topic, tracking the current version of its webhook credential.
The raw token is never stored — see app.core.webhook_auth.

Revision ID: e1f2a3b4c5d6
Revises: a8c2d4e6f901
Create Date: 2026-08-09 08:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "a8c2d4e6f901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "webhook_tokens",
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("topic_id"),
    )
    op.create_index(
        op.f("ix_webhook_tokens_project_id"),
        "webhook_tokens",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_webhook_tokens_project_id"), table_name="webhook_tokens")
    op.drop_table("webhook_tokens")
