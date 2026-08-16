"""hosted_device joined subtype and per-topic visibility (#442 steps 2-3)

Additive only: the legacy device supply/visibility/owner columns and every existing
foreign key remain in place for the dual-read window.

Revision ID: d8f4a1c2e693
Revises: b7c4e9a20d13
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d8f4a1c2e693"
down_revision: str | Sequence[str] | None = "b7c4e9a20d13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def backfill_hosted_subtype(connection: sa.Connection) -> dict[str, int]:
    hosted = connection.execute(
        sa.text(
            "INSERT INTO hosted_device (device_id, owner_user_id) "
            "SELECT device_id, owner_user_id FROM device "
            "WHERE supply = 'self_hosted' "
            "ON CONFLICT (device_id) DO UPDATE "
            "SET owner_user_id = EXCLUDED.owner_user_id"
        )
    )
    # EVERY existing binding, not just the hosted ones. A cloud endpoint has no
    # `hosted_device` row by design, so joining through it would leave every cloud
    # topic's binding on the column's `isolated` server default — and `isolated`
    # is exactly what `resolve_pinned_device` and `host_swap` refuse. Cloud devices
    # carry `host` (machine/services.py enrols them that way), so copying the
    # device's own value preserves today's behaviour for both kinds.
    bindings = connection.execute(
        sa.text(
            "UPDATE device_topic AS dt SET visibility = d.visibility "
            "FROM device AS d "
            "WHERE dt.device_id = d.device_id"
        )
    )
    return {"hosted_devices": hosted.rowcount, "topic_bindings": bindings.rowcount}


def upgrade() -> None:
    op.create_table(
        "hosted_device",
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id"], ["device.device_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id"),
    )
    op.create_index(
        op.f("ix_hosted_device_owner_user_id"),
        "hosted_device",
        ["owner_user_id"],
        unique=False,
    )
    op.add_column(
        "device_topic",
        sa.Column(
            "visibility",
            sa.String(length=16),
            nullable=False,
            server_default="isolated",
        ),
    )
    backfill_hosted_subtype(op.get_bind())


def downgrade() -> None:
    op.drop_column("device_topic", "visibility")
    op.drop_index(op.f("ix_hosted_device_owner_user_id"), table_name="hosted_device")
    op.drop_table("hosted_device")
