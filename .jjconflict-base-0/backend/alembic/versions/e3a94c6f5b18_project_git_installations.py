"""project_git_installations table (#192 GitHub App install flow)

Records which cheesex-app GitHub App installation a project is connected to
(project ↔ installation ↔ repo), populated by the install-callback route.
Both project_id and installation_id are unique: a project connects to one
repo at a time, and an installation is never shared between two projects.

Revision ID: e3a94c6f5b18
Revises: a8c2d4e6f901
Create Date: 2026-08-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e3a94c6f5b18"
down_revision: str | Sequence[str] | None = "a8c2d4e6f901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_git_installations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("repo", sa.String(length=255), nullable=False),
        sa.Column("account", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", name="uq_project_git_installations_project"),
        sa.UniqueConstraint(
            "installation_id", name="uq_project_git_installations_installation"
        ),
    )


def downgrade() -> None:
    op.drop_table("project_git_installations")
