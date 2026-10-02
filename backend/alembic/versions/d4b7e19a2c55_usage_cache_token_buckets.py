"""A usage row keeps the cache shares of its prompt tokens.

Subscription traffic is priced at the model's own rates, which bill a prompt
token read from or written to the provider's cache differently from a fresh
one, so the row records how many of its input tokens were each.

Revision ID: d4b7e19a2c55
Revises: c3e8a51f0d27
"""

import sqlalchemy as sa

from alembic import op

revision = "d4b7e19a2c55"
down_revision = "c3e8a51f0d27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "resource_usage",
        sa.Column(
            "cache_read_tokens", sa.BigInteger(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "resource_usage",
        sa.Column(
            "cache_write_tokens", sa.BigInteger(), nullable=False, server_default="0"
        ),
    )


def downgrade() -> None:
    op.drop_column("resource_usage", "cache_write_tokens")
    op.drop_column("resource_usage", "cache_read_tokens")
