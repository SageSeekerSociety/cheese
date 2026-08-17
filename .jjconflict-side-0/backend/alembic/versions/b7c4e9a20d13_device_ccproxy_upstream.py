"""device.ccproxy_upstream — a self-hosted device's own identity at ccproxy

The self-hosted twin of `project_machines.ccproxy_upstream` (a3f10c7d5b92), so
every compute form converges on ONE credential model: the device carries its own
ccproxy ticket, claude refreshes it, the platform holds no spendable credential,
and the meter relays the ticket over this identity. Without it a self-hosted
device — the dev box first among them — is forced onto the platform-credential
swap path, which is exactly the "who keeps the injected token fresh" failure
of #393: that path went down for two days when the injected token died.

A MicroCloud machine's identity is captured at enrollment; a self-hosted device
has no enrollment, so this is set by whoever administers the device. Existing
rows get NULL and stay NULL — a device that brings no identity keeps using the
platform pool, which is what every laptop-class device does today.

Revision ID: b7c4e9a20d13
Revises: a3f10c7d5b92
Create Date: 2026-08-15
"""

import sqlalchemy as sa

from alembic import op

revision = "b7c4e9a20d13"
down_revision = "a3f10c7d5b92"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("device", sa.Column("ccproxy_upstream", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("device", "ccproxy_upstream")
