"""zhishi team projects (project + project_membership)

Revision ID: 41224effe32b
Revises: a1b2c3d4e5f6
Create Date: 2026-07-13 00:59:59.527183

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "41224effe32b"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """知是 team projects (reference cheese-backend-nt project domain)."""
    op.create_table(
        "project",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("color_code", sa.String(length=7), nullable=False),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("leader_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("external_task_id", sa.BigInteger(), nullable=True),
        sa.Column("github_repo", sa.String(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_leader_id", "project", ["leader_id"], unique=False)
    op.create_index(op.f("ix_project_name"), "project", ["name"], unique=False)
    op.create_index("ix_project_parent_id", "project", ["parent_id"], unique=False)
    op.create_index("ix_project_team_id", "project", ["team_id"], unique=False)
    op.create_table(
        "project_membership",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_project_membership_project_id",
        "project_membership",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_project_membership_user_id",
        "project_membership",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "uq_project_membership_project_user",
        "project_membership",
        ["project_id", "user_id"],
        unique=True,
    )


def downgrade() -> None:
    """Drop the team-project tables."""
    op.drop_index("uq_project_membership_project_user", table_name="project_membership")
    op.drop_index("ix_project_membership_user_id", table_name="project_membership")
    op.drop_index("ix_project_membership_project_id", table_name="project_membership")
    op.drop_table("project_membership")
    op.drop_index("ix_project_team_id", table_name="project")
    op.drop_index("ix_project_parent_id", table_name="project")
    op.drop_index(op.f("ix_project_name"), table_name="project")
    op.drop_index("ix_project_leader_id", table_name="project")
    op.drop_table("project")
