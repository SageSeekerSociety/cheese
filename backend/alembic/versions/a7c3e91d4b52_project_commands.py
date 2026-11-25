"""project_commands: a project's named commands

Revision ID: a7c3e91d4b52
Revises: c5e1a7d39f20
Create Date: 2026-11-24 05:40:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c3e91d4b52"
down_revision: str | Sequence[str] | None = "c5e1a7d39f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_commands",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("fixed_by", sa.String(128), nullable=False),
        sa.Column("fixed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "name", name="uq_project_commands_name"),
    )
    op.create_index(
        "ix_project_commands_project_id", "project_commands", ["project_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_project_commands_project_id", table_name="project_commands")
    op.drop_table("project_commands")
