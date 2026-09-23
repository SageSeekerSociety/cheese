"""Retain Cloud allocation ownership per agent session."""

import sqlalchemy as sa

from alembic import op

revision = "b672a09ef831"
down_revision = "a9c7d3e4b210"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No backfill: a legacy room VM may serve several sessions. Its ownership
    # cannot be assigned to one of them without migrating the actual workspace.
    op.add_column("project_machines", sa.Column("session_id", sa.Uuid(), nullable=True))
    op.add_column(
        "project_machines",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_index("uq_project_machines_active_topic", table_name="project_machines")
    op.create_index(
        "uq_project_machines_active_topic",
        "project_machines",
        ["topic_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL AND session_id IS NULL"),
    )
    op.create_index(
        "uq_project_machines_active_session",
        "project_machines",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL AND superseded_at IS NULL"),
    )


def downgrade() -> None:
    # Downgrading while sessions hold compute would erase its owner, even if
    # there is only one allocation in the room and the old index could fit it.
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM project_machines "
            "WHERE session_id IS NOT NULL AND released_at IS NULL)"
        )
    ):
        raise RuntimeError("Release session Cloud allocations before downgrading")
    op.drop_index("uq_project_machines_active_session", table_name="project_machines")
    op.drop_index("uq_project_machines_active_topic", table_name="project_machines")
    op.create_index(
        "uq_project_machines_active_topic",
        "project_machines",
        ["topic_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.drop_column("project_machines", "session_id")
    op.drop_column("project_machines", "superseded_at")
