"""Store project sites and immutable release manifests."""

import sqlalchemy as sa

from alembic import op

revision = "f1a72c8d4e93"
down_revision = "c1f4a9b73d20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site_releases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_revision", sa.String(64), nullable=False),
        sa.Column("directory", sa.String(1024), nullable=False),
        sa.Column("entry_file", sa.String(255), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_by", sa.String(64), nullable=False),
    )
    op.create_index("ix_site_releases_project_id", "site_releases", ["project_id"])
    op.create_table(
        "sites",
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "current_release_id",
            sa.Uuid(),
            sa.ForeignKey("site_releases.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("sites")
    op.drop_table("site_releases")
