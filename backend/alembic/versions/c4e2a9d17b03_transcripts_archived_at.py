"""transcripts_archived_at on topics and tasks

A place that ran on a device leaves its raw Claude session files in the
device home, and the home is deleted at archive (topic/retire.py). Before that
deletion the device now ships those files to the platform (topic/transcripts.py),
and this column is where the place records that it happened: null until the
transcripts are stored, so the sweep and the UI can tell an archived place
whose transcripts are kept from one whose are not. Threads have their own home,
hence the same column on `tasks`.

Additive and reversible.

Revision ID: c4e2a9d17b03
Revises: b1d47f0a3c25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4e2a9d17b03"
down_revision: str | Sequence[str] | None = "b1d47f0a3c25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("topics", "tasks"):
        op.add_column(
            table,
            sa.Column(
                "transcripts_archived_at", sa.DateTime(timezone=True), nullable=True
            ),
        )


def downgrade() -> None:
    for table in ("topics", "tasks"):
        op.drop_column(table, "transcripts_archived_at")
