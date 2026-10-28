"""A person's own 芝士 acting for them is marked on what it did

Revision ID: e73ca7b0dd69
Revises: c5e8a2d71f46

It acts with the person's authority, so the records name the person; each of
these three says, beside that name, that their 芝士 did it: a task it opened
(``tasks.created_by_assistant``), a review card it accepted or rejected
(``accept_cards.decided_by_assistant``), an invitation it sent
(``project_invitations.invited_by_assistant``). A message it sent says so in
its block's meta. Rows from before were all the person's own.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "e73ca7b0dd69"
down_revision: str | Sequence[str] | None = "c5e8a2d71f46"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    ("tasks", "created_by_assistant"),
    ("accept_cards", "decided_by_assistant"),
    ("project_invitations", "invited_by_assistant"),
)


def upgrade() -> None:
    with_lock_retries("tasks, accept_cards, project_invitations")
    for table, column in COLUMNS:
        op.add_column(
            table,
            sa.Column(column, sa.Boolean(), server_default="false", nullable=False),
        )


def downgrade() -> None:
    for table, column in COLUMNS:
        op.drop_column(table, column)
