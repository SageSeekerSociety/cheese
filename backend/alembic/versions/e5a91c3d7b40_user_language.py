"""A user keeps the UI language they picked.

Push and desktop notifications are written on the server, so the server has to
know which language each recipient reads. NULL is someone who has not picked
one yet; they are written to in Chinese, as before this column existed, so the
column needs no backfill and the release before it never reads it.

Revision ID: e5a91c3d7b40
Revises: f9be82f700e2
"""

import sqlalchemy as sa

from alembic import op

revision = "e5a91c3d7b40"
down_revision = "f9be82f700e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user", sa.Column("language", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("user", "language")
