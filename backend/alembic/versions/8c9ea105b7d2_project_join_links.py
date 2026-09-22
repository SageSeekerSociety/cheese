"""Add revocable project invitation links."""

import sqlalchemy as sa

from alembic import op

revision = "8c9ea105b7d2"
down_revision = "93ac2aa4b5fd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_join_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("token", sa.String(64), nullable=False, unique=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("project_join_links")
