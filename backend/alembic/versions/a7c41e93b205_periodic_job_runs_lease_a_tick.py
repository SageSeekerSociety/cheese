"""periodic_job_runs: a tick is leased to the process running it

`last_run_at` says when a job last ran; it cannot say who is running it now, or
until when that claim stands. A job moving out of the global ownership lock
(docs/manual/dev/architecture.md, 迁移顺序 2e) is scheduled on every process, so
"exactly one process runs this tick" has to live in the row instead: the claim
is an INSERT … ON CONFLICT DO UPDATE … RETURNING, and it only returns a row if
the previous run is due AND its lease has run out.

Both columns are nullable: the rows a previous release wrote have no value for
either, and that release keeps serving until the new one is up.

Revision ID: a7c41e93b205
Revises: 9f2b7c14a8e3
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "a7c41e93b205"
down_revision: str | Sequence[str] | None = "0c800ff1db2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("periodic_job_runs")
    op.add_column(
        "periodic_job_runs",
        sa.Column("run_by", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "periodic_job_runs",
        sa.Column("run_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    with_lock_retries("periodic_job_runs")
    op.drop_column("periodic_job_runs", "run_until")
    op.drop_column("periodic_job_runs", "run_by")
