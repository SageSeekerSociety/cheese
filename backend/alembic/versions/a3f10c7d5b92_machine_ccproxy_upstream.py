"""project_machines.ccproxy_upstream — the machine's own identity at ccproxy

ccproxy scopes its fake→real ticket swap to the identity the proxy connection
authenticated as. Measured 2026-08-14: a ticket issued to machine m516, replayed
over a connection authenticating as m161, comes back `401 OAuth access token is
invalid` with no request_id; the same ticket over m516's own connection reaches
Anthropic. So the meter can only forward a machine's own ticket untouched if it
also presents that machine's identity — which is what this column stores
(`user:password`, as MicroCloud wrote it into the machine's settings.json).

Existing rows get NULL and stay NULL: it is read during enrollment, the only
moment the platform is on the machine over ssh (the bootstrap key is erased the
instant enrollment succeeds). NULL is a supported steady state rather than a gap
to backfill — the meter falls back to the deployment-wide identity and its old
credential swap, which is exactly what those machines do today.

Revision ID: a3f10c7d5b92
Revises: 5fcb9dff4746
Create Date: 2026-08-14
"""

import sqlalchemy as sa

from alembic import op

revision = "a3f10c7d5b92"
down_revision = "5fcb9dff4746"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_machines",
        sa.Column("ccproxy_upstream", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_machines", "ccproxy_upstream")
