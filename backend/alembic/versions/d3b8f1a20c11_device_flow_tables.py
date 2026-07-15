"""device flow tables: device + device_auth_code + device_project (self-hosted P3)

Revision ID: d3b8f1a20c11
Revises: c5f1a9d24e07
Create Date: 2026-07-09 00:00:00.000000

The self-hosted / BYO-compute device flow (fusion-design §5): an enrolled compute
machine (``device``) holds a durable token; short-lived device-flow codes live in
``device_auth_code``; ``device_project`` records which projects a device may run agents
in. A device is PURE COMPUTE (execution-architecture v3: a ComputePool node) — it holds
no agent identity. Additive only — no existing table is touched.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3b8f1a20c11"
down_revision: str | Sequence[str] | None = "c5f1a9d24e07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = (
    "a95752502bb0"  # fusion A2: needs main user table
)


def upgrade() -> None:
    op.create_table(
        "device",
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id"),
    )
    op.create_index(op.f("ix_device_token"), "device", ["token"], unique=True)
    op.create_index(
        op.f("ix_device_owner_user_id"), "device", ["owner_user_id"], unique=False
    )

    op.create_table(
        "device_auth_code",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("device_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )

    op.create_table(
        "device_project",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id"], ["device.device_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "project_id", name="uq_device_project"),
    )
    op.create_index(
        op.f("ix_device_project_device_id"),
        "device_project",
        ["device_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_device_project_project_id"),
        "device_project",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_device_project_project_id"), table_name="device_project")
    op.drop_index(op.f("ix_device_project_device_id"), table_name="device_project")
    op.drop_table("device_project")
    op.drop_table("device_auth_code")
    op.drop_index(op.f("ix_device_owner_user_id"), table_name="device")
    op.drop_index(op.f("ix_device_token"), table_name="device")
    op.drop_table("device")
