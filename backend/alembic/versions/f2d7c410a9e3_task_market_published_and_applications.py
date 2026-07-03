"""task market: template published flag + task_applications table

匹配市场 (spec §13 阶段 6): a Space publishes a Task Template to the market
(published), teams apply with a Project (task_applications), accept creates a
Task + ProjectTaskLink. One application per (template, project).

Revision ID: f2d7c410a9e3
Revises: c3a8f5d19e42
Create Date: 2026-07-03 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2d7c410a9e3"
down_revision: str | Sequence[str] | None = "c3a8f5d19e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "task_templates",
        sa.Column(
            "published", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.create_table(
        "task_applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("pitch", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["template_id"], ["task_templates.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_id", "project_id", name="uq_task_application"),
    )
    op.create_index(
        op.f("ix_task_applications_template_id"),
        "task_applications",
        ["template_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_task_applications_project_id"),
        "task_applications",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_task_applications_project_id"), table_name="task_applications"
    )
    op.drop_index(
        op.f("ix_task_applications_template_id"), table_name="task_applications"
    )
    op.drop_table("task_applications")
    op.drop_column("task_templates", "published")
