"""tasks wait for their deploy

Revision ID: b7d3e91c4a20
Revises: b3f7c1d94a20
Create Date: 2026-11-16

A task whose last delivery merged in a project whose deploy reports back waits,
open, until a deploy containing the merge is live (`tasks.deploying_since`),
with the red run of main's Required CI that holds it back, if any
(`tasks.deploy_blocked_url`). Each report a deploy sends is kept
(`deploy_reports`): a platform no deploy reports to does not make tasks wait.
Both columns are nullable and the table is new, so the running backend keeps
working while this applies.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "b7d3e91c4a20"
down_revision: str | Sequence[str] | None = "b3f7c1d94a20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deploy_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo", sa.String(255), nullable=False),
        sa.Column("sha", sa.String(64), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deploy_reports_repo", "deploy_reports", ["repo"], unique=False)
    with_lock_retries("tasks")
    op.add_column(
        "tasks",
        sa.Column("deploying_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks", sa.Column("deploy_blocked_url", sa.String(500), nullable=True)
    )


def downgrade() -> None:
    with_lock_retries("tasks")
    op.drop_column("tasks", "deploy_blocked_url")
    op.drop_column("tasks", "deploying_since")
    op.drop_index("ix_deploy_reports_repo", table_name="deploy_reports")
    op.drop_table("deploy_reports")
