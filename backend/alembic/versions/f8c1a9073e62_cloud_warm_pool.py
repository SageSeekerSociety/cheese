"""Add durable platform warm capacity and claim reservations."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "f8c1a9073e62"
down_revision = "b7e4d21c9a06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_machines",
        sa.Column(
            "warm_claim_pending",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_table(
        "warm_machines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("machine_id", sa.BigInteger(), nullable=True, unique=True),
        sa.Column("create_request", postgresql.JSONB(), nullable=False),
        sa.Column("bootstrap_key", sa.Text(), nullable=True),
        sa.Column("device_id", sa.String(64), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("ccproxy_upstream", sa.Text(), nullable=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "claimed_machine_id",
            sa.Uuid(),
            sa.ForeignKey("project_machines.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
    )
    op.create_index("ix_warm_machines_state", "warm_machines", ["state"])


def downgrade() -> None:
    op.drop_table("warm_machines")
    op.drop_column("project_machines", "warm_claim_pending")
