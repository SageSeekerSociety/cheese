"""session_states.run_epoch/run_seq, tasks.progress_run_*: which run a report was made in

Revision ID: c5e8a1d47b93
Revises: b8d3f1a6c254

A task whose teammate's session is running reads 在做 unless the teammate
reported in that same run. The run is named by the session's runner: the
(epoch, seq) of the `running` event that began it. `session_states.run_epoch`
/ `run_seq` hold the run the seat's current process last began, and a report
carries the run it was made in (the `X-Cheese-Run` header of its platform
call), kept in `tasks.progress_run_harness` / `progress_run_epoch` /
`progress_run_seq` beside the seat that made it (`progress_set_by`).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "c5e8a1d47b93"
down_revision: str | Sequence[str] | None = "b8d3f1a6c254"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("session_states, tasks")
    op.add_column(
        "session_states", sa.Column("run_epoch", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "session_states", sa.Column("run_seq", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "tasks", sa.Column("progress_run_harness", sa.String(32), nullable=True)
    )
    op.add_column(
        "tasks", sa.Column("progress_run_epoch", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "tasks", sa.Column("progress_run_seq", sa.BigInteger(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("tasks", "progress_run_seq")
    op.drop_column("tasks", "progress_run_epoch")
    op.drop_column("tasks", "progress_run_harness")
    op.drop_column("session_states", "run_seq")
    op.drop_column("session_states", "run_epoch")
