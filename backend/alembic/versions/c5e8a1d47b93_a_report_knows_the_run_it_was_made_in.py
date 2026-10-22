"""session_states.run, tasks.progress_run: which run of a seat's session a report was made in

Revision ID: c5e8a1d47b93
Revises: b7d3e19a4c52

A task whose teammate's session is running reads 在做 unless the teammate
reported during that run. "During that run" is decided by the run's identity,
not by comparing when the platform applied the runner's event with when the
report arrived: `session_states.run` counts the seat's runs (it goes up each
time the runner declares the session running), and `tasks.progress_run` is the
run of the reporting seat the report was made in.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "c5e8a1d47b93"
down_revision: str | Sequence[str] | None = "b7d3e19a4c52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("session_states, tasks")
    op.add_column(
        "session_states",
        sa.Column("run", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column("tasks", sa.Column("progress_run", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "progress_run")
    op.drop_column("session_states", "run")
