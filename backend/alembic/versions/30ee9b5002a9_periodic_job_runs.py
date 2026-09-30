"""periodic job runs

Revision ID: 30ee9b5002a9
Revises: c4e18a72b9d0

When each periodic job last ran, so a restarted backend runs a job as soon as it
is overdue instead of counting its interval from the restart
(`app.core.job_runs`).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "30ee9b5002a9"
down_revision: str | Sequence[str] | None = "c4e18a72b9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "periodic_job_runs",
        sa.Column("name", sa.String(100), primary_key=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("periodic_job_runs")
