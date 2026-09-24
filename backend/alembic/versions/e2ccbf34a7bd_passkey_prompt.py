"""Remember how each account answered the offer to add a passkey

The offer after a password sign-in backs off when declined: 30 days, then 90,
then never. That has to hold on every device the person signs in from, so it
lives with the account rather than in the browser.

Revision ID: e2ccbf34a7bd
Revises: b3a91c7e52f0
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e2ccbf34a7bd"
down_revision: str | Sequence[str] | None = "b3a91c7e52f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "passkey_prompt",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "dismissals", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ended", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("passkey_prompt")
