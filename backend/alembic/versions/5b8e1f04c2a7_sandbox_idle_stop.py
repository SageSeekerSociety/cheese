"""A cloud sandbox stops when idle, and its home can be archived off its host.

``cloud_host_homes`` learns when its session was last active, when its sandbox
was stopped, who is moving it, where its archive is, and why its last archive
failed. An archived home is on
no host, so ``host_id`` may be NULL.
"""

import sqlalchemy as sa

from alembic import op

revision = "5b8e1f04c2a7"
down_revision = "c4e7a2d91f30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("cloud_host_homes", "host_id", nullable=True)
    op.add_column(
        "cloud_host_homes",
        sa.Column(
            "active_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    for name in ("stopped_at", "busy_until", "archive_failed_at"):
        op.add_column(
            "cloud_host_homes",
            sa.Column(name, sa.DateTime(timezone=True), nullable=True),
        )
    op.add_column("cloud_host_homes", sa.Column("archive_key", sa.Text()))
    op.add_column("cloud_host_homes", sa.Column("archive_size", sa.BigInteger()))
    op.add_column("cloud_host_homes", sa.Column("archive_md5", sa.String(32)))
    op.add_column("cloud_host_homes", sa.Column("archive_error", sa.Text()))


def downgrade() -> None:
    # An archived home is on no host; the schema before this has nowhere to
    # say where its work is.
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM cloud_host_homes WHERE host_id IS NULL)")
    ):
        raise RuntimeError("Restore or clean up every archived home before downgrading")
    for name in (
        "archive_error",
        "archive_failed_at",
        "archive_md5",
        "archive_size",
        "archive_key",
        "busy_until",
        "stopped_at",
        "active_at",
    ):
        op.drop_column("cloud_host_homes", name)
    op.alter_column("cloud_host_homes", "host_id", nullable=False)
