"""cloud_hosts can be brought up to date in place

A host whose enrollment script is all that changed is no longer rebuilt: once
no sandbox but the parked ones is on it, the script runs again on the machine,
and its sandbox homes, package stores, repository copy and parked sandboxes
stay. `refresh_in_place` says a draining host goes that way; `refresh_started_at`
is when the run began on the machine.

Both are additive: the release still serving while the migration runs never
reads them, and the default (false, NULL) is how it treats every host now.

Revision ID: a7c3e9f1b5d2
Revises: e9d4b023c781
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "a7c3e9f1b5d2"
down_revision: str | Sequence[str] | None = "e9d4b023c781"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HOSTS = "cloud_hosts"


def upgrade() -> None:
    with_lock_retries(HOSTS)
    op.add_column(
        HOSTS,
        sa.Column(
            "refresh_in_place",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        HOSTS,
        sa.Column("refresh_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    with_lock_retries(HOSTS)
    op.drop_column(HOSTS, "refresh_started_at")
    op.drop_column(HOSTS, "refresh_in_place")
