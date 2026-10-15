"""A routine waits for nobody

Revision ID: 0d7f3a9c41b6
Revises: 49a2e86fa493
Create Date: 2026-10-15

「待确认」 is gone: a rule is saved and running, whoever made it. A rule left
waiting for a person to confirm it (`state = 'draft'`) becomes `paused`.
Nobody ever approved it to run, and the state it was waiting in no longer
exists; whoever wanted it turns it on with 「恢复」, the same click as any
paused rule. Nothing else is a candidate: `active` would start unattended work
nobody asked for, and the run the rule would have queued has no `next_run_at`
to fire at.

The columns `proposed_by`, `confirmed_by` and `confirmed_at` stay, because the
release still serving maps them. This one stops mapping the last two — they go
two releases from now — and `created_by` is the new name on the `proposed_by`
column, not a rename.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0d7f3a9c41b6"
down_revision: str | Sequence[str] | None = "49a2e86fa493"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE routines SET state = 'paused' WHERE state = 'draft'")


def downgrade() -> None:
    """Nothing to undo, and no way to: once a waiting rule is paused, the row
    is identical to a rule a person paused, so it cannot be told apart again.
    The downgrade leaves both where they are."""
