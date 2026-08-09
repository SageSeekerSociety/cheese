"""device_topic: a topic's pinned device (compute affinity, execution-architecture v4)

A topic's work tree + resumable claude session live on ONE self-hosted machine, so
every turn of that topic must run on the same device. ``device_topic`` records that
pin (topic_id → device_id), written once on the topic's first turn; later turns return
to the pinned device and never drift to another online one. Additive only.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7c2a4f19d33"
down_revision: str | Sequence[str] | None = "41224effe32b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_topic",
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id"], ["device.device_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("topic_id"),
    )
    op.create_index(
        op.f("ix_device_topic_device_id"),
        "device_topic",
        ["device_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_device_topic_device_id"), table_name="device_topic")
    op.drop_table("device_topic")
