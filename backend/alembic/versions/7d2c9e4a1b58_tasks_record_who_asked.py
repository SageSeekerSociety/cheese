"""tasks.asker_handles: the members a request was passed on to a task for

Revision ID: 7d2c9e4a1b58
Revises: 59f3a1c11b34
Create Date: 2026-11-17

A channel's main line may pass a member's small request on to a task already
working where it lands (`cheese_task(action=tell)` with ``asker``) instead of
opening a new one. Each member it was passed on for is kept here, so when the
task is handed in or ends, the main line is told everyone who asked. Empty for
every existing task: nothing was passed on before this release.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "7d2c9e4a1b58"
down_revision: str | Sequence[str] | None = "59f3a1c11b34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("tasks")
    op.add_column(
        "tasks",
        sa.Column(
            "asker_handles",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "asker_handles")
