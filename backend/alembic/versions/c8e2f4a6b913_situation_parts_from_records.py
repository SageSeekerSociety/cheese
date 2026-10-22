"""project_situations: the 「现状」 is parts with sources, no longer Markdown

Revision ID: c8e2f4a6b913
Revises: a3c7e1f9d240
Create Date: 2026-10-22

The card is now the parts the backend checked against the project's records,
each with its status and sources: `parts` (JSONB) replaces `content` (the
model's Markdown). `minor` was the mark for channel talk, which no longer feeds
the card. The code stops mapping both here; they are dropped in a later
release, once nothing serving still maps them (two-release drop).

No backfill: Markdown cannot be turned into checked parts, so every row is
marked unwritten and is rewritten the next time its project's overview is
opened.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c8e2f4a6b913"
down_revision: str | Sequence[str] | None = "a3c7e1f9d240"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("project_situations")
    op.add_column(
        "project_situations",
        sa.Column(
            "parts",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.execute("UPDATE project_situations SET written_at = NULL, major = ''")


def downgrade() -> None:
    with_lock_retries("project_situations")
    op.drop_column("project_situations", "parts")
    op.execute("UPDATE project_situations SET written_at = NULL, major = ''")
