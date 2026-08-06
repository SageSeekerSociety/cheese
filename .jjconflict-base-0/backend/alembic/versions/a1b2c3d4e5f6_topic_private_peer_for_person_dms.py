"""topic.private_peer for person-to-person 私聊 (peer DMs)

Adds a nullable `private_peer` handle to topics. A private topic is now either
a 芝士 DM (private_peer NULL, private_owner = member handle) or a peer DM
between two humans, where the unordered handle pair is canonicalized as
private_owner = min(a, b), private_peer = max(a, b) so both users share one row.

Revision ID: a1b2c3d4e5f6
Revises: d1a2b3c4e5f6
Create Date: 2026-07-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "d1a2b3c4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "topics",
        sa.Column("private_peer", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("topics", "private_peer")
