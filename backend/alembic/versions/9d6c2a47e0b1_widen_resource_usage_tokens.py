"""Allow a gateway daily delta to exceed the signed 32-bit token limit.

Revision ID: 9d6c2a47e0b1
Revises: ed50d103eb11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9d6c2a47e0b1"
down_revision: str | Sequence[str] | None = "ed50d103eb11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in ("input_tokens", "output_tokens", "total_tokens"):
        op.alter_column(
            "resource_usage",
            column,
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )


def downgrade() -> None:
    for column in ("input_tokens", "output_tokens", "total_tokens"):
        op.alter_column(
            "resource_usage",
            column,
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=False,
        )
