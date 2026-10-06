"""cloud_hosts.offline_since: since when an enrolled host's connector is away

The pool gives up on an enrolled host whose connector has been away for
`services.LOST_AFTER` while another host of the pool is online, and places its
sessions in new sandboxes (on 2026-10-05 dev, sessions waited four to five hours
on a host whose sshd hung). How long it has been away is counted by the pool
sweep and kept here, so a backend restart does not start the count again.

Revision ID: b5d81e3c07a9
Revises: b672fdeb358e
Create Date: 2026-10-06 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b5d81e3c07a9"
down_revision: str | Sequence[str] | None = "b672fdeb358e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cloud_hosts",
        sa.Column("offline_since", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cloud_hosts", "offline_since")
