"""topic-owned cloud machines and release lifecycle (#442 step 5)

Existing project-machine rows remain project-scoped/manual: both new columns are
NULL, so no historical machine is silently claimed by a topic or reclaimed.

Revision ID: e6c8a12f4b90
Revises: d8f4a1c2e693
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e6c8a12f4b90"
down_revision: str | Sequence[str] | None = "d8f4a1c2e693"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("project_machines", sa.Column("topic_id", sa.Uuid(), nullable=True))
    op.add_column(
        "project_machines",
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_project_machines_topic_id",
        "project_machines",
        "topics",
        ["topic_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "uq_project_machines_active_topic",
        "project_machines",
        ["topic_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_project_machines_active_topic", table_name="project_machines")
    op.drop_constraint(
        "fk_project_machines_topic_id", "project_machines", type_="foreignkey"
    )
    op.drop_column("project_machines", "released_at")
    op.drop_column("project_machines", "topic_id")
