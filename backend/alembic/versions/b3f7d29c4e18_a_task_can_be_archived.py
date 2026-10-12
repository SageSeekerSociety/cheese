"""tasks.archived_at: a task its owner puts away

A channel has had 归档 since the beginning; a task only had 关闭. Closing keeps
the task where it was and says it is over, so a finished piece of work stays in
the list for good — thousands of them per channel on dev. Archiving is the other
act: the task leaves the lists and is read-only, and can be brought back.

A timestamp rather than a third `TaskStatus` value: that enum's two values say
"is this work still going", and an archived task is closed first, so `open` and
`archived` never hold at once and every `status == open` judgement stays right
without knowing this column exists.

Additive: the previous image neither reads nor writes it, and NULL — "not
archived" — is right for every task that already exists.

Revision ID: b3f7d29c4e18
Revises: f175b976a14a
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "b3f7d29c4e18"
down_revision: str | Sequence[str] | None = "f175b976a14a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("tasks")
    op.add_column(
        "tasks", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("tasks", "archived_at")
