"""host_readings and sandbox_usages: what each platform host and sandbox uses

Revision ID: 4e8b1d6a2c90
Revises: c7d41e9a2f05
Create Date: 2026-10-16

The disk guard's reading of a host, kept for the admin machines page: one
``host_readings`` row a host a check, pruned after a few hours, and one
``sandbox_usages`` row a sandbox with its latest memory and disk. New tables,
so no backfill: they fill on the next check.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "4e8b1d6a2c90"
down_revision: str | Sequence[str] | None = "c7d41e9a2f05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The foreign keys take locks on cloud_hosts and sandbox_homes, which the
    # live backend writes.
    with_lock_retries("cloud_hosts, sandbox_homes")
    op.create_table(
        "host_readings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("host_id", sa.Uuid(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disk_bytes", sa.BigInteger(), nullable=False),
        sa.Column("disk_free_bytes", sa.BigInteger(), nullable=False),
        sa.Column("inodes", sa.BigInteger(), nullable=True),
        sa.Column("inodes_free", sa.BigInteger(), nullable=True),
        sa.Column("memory_bytes", sa.BigInteger(), nullable=True),
        sa.Column("memory_available_bytes", sa.BigInteger(), nullable=True),
        sa.Column("cpu_usage_usec", sa.BigInteger(), nullable=True),
        sa.Column("load1", sa.Float(), nullable=True),
        sa.Column("sandboxes", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["host_id"],
            ["cloud_hosts.id"],
            ondelete="CASCADE",
            name="fk_host_readings_host_id_cloud_hosts",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_host_readings_host_id_read_at", "host_readings", ["host_id", "read_at"]
    )
    op.create_table(
        "sandbox_usages",
        sa.Column("home_id", sa.Uuid(), nullable=False),
        sa.Column("memory_bytes", sa.BigInteger(), nullable=True),
        sa.Column("memory_peak_bytes", sa.BigInteger(), nullable=True),
        sa.Column("memory_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disk_bytes", sa.BigInteger(), nullable=True),
        sa.Column("disk_inodes", sa.BigInteger(), nullable=True),
        sa.Column("disk_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["home_id"],
            ["sandbox_homes.id"],
            ondelete="CASCADE",
            name="fk_sandbox_usages_home_id_sandbox_homes",
        ),
        sa.PrimaryKeyConstraint("home_id"),
    )


def downgrade() -> None:
    op.drop_table("sandbox_usages")
    op.drop_index("ix_host_readings_host_id_read_at", table_name="host_readings")
    op.drop_table("host_readings")
