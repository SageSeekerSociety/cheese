"""A session can have a whole cloud VM of its own.

``cloud_hosts`` learns which rows are such VMs and whose project each was
created for; ``cloud_host_homes`` learns when its session last asked for a
tool, which with the room's turns says when a VM is idle.
"""

import sqlalchemy as sa

from alembic import op

revision = "7d3a9c61e2b4"
down_revision = "c4e7a2d91f30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cloud_hosts",
        sa.Column(
            "whole_machine",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "cloud_hosts",
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "cloud_host_homes",
        sa.Column(
            "active_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    # A VM row would read as a pool host with sandbox slots under the old
    # schema, and be handed other sessions.
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM cloud_hosts "
            "WHERE whole_machine AND released_at IS NULL)"
        )
    ):
        raise RuntimeError("Release every whole cloud VM before downgrading")
    op.drop_column("cloud_host_homes", "active_at")
    op.drop_column("cloud_hosts", "project_id")
    op.drop_column("cloud_hosts", "whole_machine")
