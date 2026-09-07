"""Store the runtime cloud machine limit.

Revision ID: d8a6b5c4e731
Revises: c2d7e9f1a718
"""

import sqlalchemy as sa

from alembic import op

revision = "d8a6b5c4e731"
down_revision = "c2d7e9f1a718"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "machine_limit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_machine_limit_singleton"),
        sa.CheckConstraint("value > 0", name="ck_machine_limit_positive"),
    )


def downgrade() -> None:
    op.drop_table("machine_limit")
