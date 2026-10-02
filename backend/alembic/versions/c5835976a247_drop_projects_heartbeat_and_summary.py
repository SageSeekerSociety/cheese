"""Drop projects.last_heartbeat_at and projects.summary

Both belonged to turns that are gone: the periodic heartbeat (#2323) wrote
`last_heartbeat_at`, and the one-pager (`summarize_project`, #2366) was the
only writer of `summary`. Neither column is mapped by the `Project` model any
more.

A deploy runs this migration while the previous backend image still serves,
so it may only land once that image no longer maps either column. On dev the
previous image is the main commit before this one, which already lacks both.
The device-connection owner, which keeps an older image across app releases,
reads `projects` only through named columns (`domain/device/owner_reads.py`).

The downgrade brings both columns back with their old defaults: the heartbeat
column empty and the summary column as empty strings. The values are not
recoverable; dev held no project with either one set.

Revision ID: c5835976a247
Revises: 2733a598f271
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5835976a247"
down_revision: str | Sequence[str] | None = "2733a598f271"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("projects", "last_heartbeat_at")
    op.drop_column("projects", "summary")


def downgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "projects",
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
