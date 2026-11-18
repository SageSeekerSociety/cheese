"""a task closed by its main line keeps the conclusion while it waits to deploy

Revision ID: efcb2b3821d5
Revises: 342495d871e3
Create Date: 2026-11-18

The channel's main line closes a task it opened once it judges it done. In a
project whose deploy reports back, a close after a merge leaves the task open
until that merge is live (`tasks.deploying_since`), and closes it then with the
conclusion the main line gave. That conclusion is held here meanwhile
(`tasks.held_conclusion`), apart from `tasks.conclusion`, which every reader
shows as how the task ended. The column is nullable, so the running backend
keeps working while this applies.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "efcb2b3821d5"
down_revision: str | Sequence[str] | None = "342495d871e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("tasks")
    op.add_column("tasks", sa.Column("held_conclusion", sa.Text(), nullable=True))


def downgrade() -> None:
    with_lock_retries("tasks")
    op.drop_column("tasks", "held_conclusion")
