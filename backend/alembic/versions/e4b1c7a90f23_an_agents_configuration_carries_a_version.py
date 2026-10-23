"""an agent's configuration carries a version

Two people editing one teammate's skills used to lose one of the two silently:
`PUT /projects/{p}/agents/{a}` replaces the whole `configuration` JSON, and
neither side had anything to notice the other's write with. The row now carries
a `version` that a save sends back, and a save whose version is stale is refused
instead of overwriting.

Additive and zero-downtime: existing rows get 1 from the server default, and the
image being replaced still writes the column's neighbours without naming it.

Revision ID: e4b1c7a90f23
Revises: b3f7a1c9d2e4
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "e4b1c7a90f23"
down_revision: str | Sequence[str] | None = "b3f7a1c9d2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("agent_instances")
    op.add_column(
        "agent_instances",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("agent_instances", "version")
