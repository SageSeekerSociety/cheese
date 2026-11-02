"""cloud hosts remember which projects' stores they keep

A sandbox of a project placed on a host leaves that project's dependencies and
build cache there (its store). Placement prefers a host that has placed the
same project before, so the next sandbox of it starts warm instead of
downloading and building everything again. This table is that memory: one row
a (host, project), written whenever a home is placed, kept after the home is
destroyed, and removed with its host.

The table is new, so the release serving while the migration runs neither
reads nor writes it.

Revision ID: c4d8e2a1f6b3
Revises: a3e6c1f9d2b7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "c4d8e2a1f6b3"
down_revision: str | Sequence[str] | None = "a3e6c1f9d2b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HOSTS = "cloud_hosts"


def upgrade() -> None:
    with_lock_retries(HOSTS)
    op.create_table(
        "host_project_stores",
        sa.Column("host_id", sa.Uuid(), nullable=False),
        sa.Column("project_key", sa.Uuid(), nullable=False),
        sa.Column("last_placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["host_id"],
            ["cloud_hosts.id"],
            ondelete="CASCADE",
            name="fk_host_project_stores_host_id_cloud_hosts",
        ),
        sa.PrimaryKeyConstraint("host_id", "project_key"),
    )
    op.create_index(
        "ix_host_project_stores_project_key",
        "host_project_stores",
        ["project_key"],
    )


def downgrade() -> None:
    with_lock_retries(HOSTS)
    op.drop_table("host_project_stores")
