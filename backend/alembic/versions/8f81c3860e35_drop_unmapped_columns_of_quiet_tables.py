"""Drop columns that the serving release no longer maps

Revision ID: 8f81c3860e35
Revises: eea562c48383
Create Date: 2026-10-20

Each went unmapped in a release that is now deployed, the first half of a
drop (rule 4 of `.claude/rules/migrations.md`):

- `routines.confirmed_at`, `confirmed_by`, `output_dir`: a routine runs
  untended and keeps its note elsewhere (#3989);
- `document_versions.requested_by`, `web_snippet_versions.requested_by`: an
  AI teammate's edit is its own (#3797);
- `native_inputs.completed_at`: the runner reports a session's lifecycle
  (#4459).

The downgrade adds the columns back, empty.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "8f81c3860e35"
down_revision: str | Sequence[str] | None = "eea562c48383"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries(
        "routines, document_versions, web_snippet_versions, native_inputs"
    )
    op.drop_column("routines", "confirmed_at")
    op.drop_column("routines", "confirmed_by")
    op.drop_column("routines", "output_dir")
    op.drop_column("document_versions", "requested_by")
    op.drop_column("web_snippet_versions", "requested_by")
    op.drop_column("native_inputs", "completed_at")


def downgrade() -> None:
    op.add_column(
        "native_inputs",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "web_snippet_versions",
        sa.Column("requested_by", sa.String(128), nullable=True),
    )
    op.add_column(
        "document_versions", sa.Column("requested_by", sa.String(128), nullable=True)
    )
    op.add_column(
        "routines",
        sa.Column("output_dir", sa.String(300), server_default="", nullable=False),
    )
    op.add_column("routines", sa.Column("confirmed_by", sa.String(64), nullable=True))
    op.add_column(
        "routines",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
