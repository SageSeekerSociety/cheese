"""ingest_checkpoints — exactly-once marker for usage-log ingestion

Revision ID: c4e8f19b0d73
Revises: a7c31e05d442
Create Date: 2026-08-10 17:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e8f19b0d73"
down_revision: str | Sequence[str] | None = "a7c31e05d442"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "ingest_checkpoints",
        sa.Column("source", sa.String(128), primary_key=True),
        sa.Column("byte_offset", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("fingerprint", sa.String(64), nullable=False, server_default=""),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("ingest_checkpoints")
