"""Persist addressed agent delivery attempts and scheduled-event materialization."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "4d0e7a91c203"
down_revision = "b672a09ef831"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "delivery_channels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("delivery_key", sa.String(160), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("receiver_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.UniqueConstraint("delivery_key", "channel", name="uq_delivery_channel"),
    )
    op.add_column(
        "deliveries",
        sa.Column(
            "external_channels", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    # Existing rows may already have entered Redis; never reconstruct their
    # external sends from mailbox state. Keep the server default false for old
    # producers during rollout; new producers explicitly write the ORM default.
    op.alter_column("deliveries", "receiver_id", nullable=True)
    for column in (
        sa.Column("agent_instance_id", sa.Uuid(), nullable=True),
        sa.Column("topic_id", sa.Uuid(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("state", sa.String(24), server_default="pending", nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    ):
        op.add_column("deliveries", column)
    for column in (
        sa.Column("materialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("agent_instance_id", sa.Uuid(), nullable=True),
        sa.Column("receiver_id", sa.BigInteger(), nullable=True),
    ):
        op.add_column("timed_deliveries", column)
    op.create_unique_constraint(
        "uq_timed_delivery_event", "timed_deliveries", ["event_id"]
    )
    op.add_column(
        "tasks", sa.Column("execution_agent_instance_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "tasks", sa.Column("execution_parent_session_id", sa.String(128), nullable=True)
    )
    op.add_column("tasks", sa.Column("execution_turn_id", sa.Uuid(), nullable=True))
    # Legacy completion does not establish receipt. Preserve its exclusion from
    # the due scan rather than replaying historical requests whose outcome is unknown.
    op.execute(
        "UPDATE timed_deliveries SET materialized_at = delivered_at WHERE delivered_at IS NOT NULL"
    )
    op.create_index(
        "idx_delivery_agent_pending",
        "deliveries",
        ["recorded_at"],
        postgresql_where=sa.text("agent_instance_id IS NOT NULL AND sent_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("delivery_channels")
    for name in (
        "execution_agent_instance_id",
        "execution_parent_session_id",
        "execution_turn_id",
    ):
        op.drop_column("tasks", name)
    op.drop_index("idx_delivery_agent_pending", table_name="deliveries")
    op.drop_constraint("uq_timed_delivery_event", "timed_deliveries", type_="unique")
    for name in ("materialized_at", "event_id", "agent_instance_id", "receiver_id"):
        op.drop_column("timed_deliveries", name)
    # Downgrade must refuse if agent intent exists; silently deleting it loses work.
    op.alter_column("deliveries", "receiver_id", nullable=False)
    for name in (
        "external_channels",
        "agent_instance_id",
        "topic_id",
        "task_id",
        "state",
        "attempt_id",
        "lease_until",
        "retry_at",
        "last_error",
    ):
        op.drop_column("deliveries", name)
