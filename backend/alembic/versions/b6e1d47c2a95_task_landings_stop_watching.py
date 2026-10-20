"""Nothing is watched after a merge: task_landings keeps only the landing

Revision ID: b6e1d47c2a95
Revises: 7a64b5d89c12
Create Date: 2026-10-19

The platform no longer watches a merge on the default branch. `task_landings`
stays as the record that a merge landed on its task once; the code stops
mapping `merge_sha`, `watch_until`, `checks`, `deploy` and `environment`, and
the next release drops them with their index, and the `culprit_searches`
table the watch's culprit search kept (rule 4 of
`.claude/rules/migrations.md`).

`watch_until` is the one of them with no default, so new rows could not be
written without it: it becomes nullable now. The previous release still sets
it, which this allows.
"""

from collections.abc import Sequence

from migration_helpers import with_lock_retries

from alembic import op

revision: str = "b6e1d47c2a95"
down_revision: str | Sequence[str] | None = "7a64b5d89c12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("task_landings")
    op.alter_column("task_landings", "watch_until", nullable=True)


def downgrade() -> None:
    with_lock_retries("task_landings")
    op.execute(
        "UPDATE task_landings SET watch_until = landed_at WHERE watch_until IS NULL"
    )
    op.alter_column("task_landings", "watch_until", nullable=False)
